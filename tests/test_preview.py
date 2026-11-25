"""Small-preview contract: temporary synthetic inputs; real trial is a separate explicit tool."""

import ast
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel.stages import preview


def paths(tmp_path):
    src, out = tmp_path / "final_blurred.mp4", tmp_path / "preview.mp4"
    src.write_bytes(b"synthetic blurred fixture")
    return src, out


def test_command_is_bounded_low_priority_and_atomic(tmp_path, monkeypatch):
    src, out = paths(tmp_path)
    out.write_bytes(b"previous good preview")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert out.read_bytes() == b"previous good preview"
        Path(cmd[-1]).write_bytes(b"new preview")

    monkeypatch.setattr(preview, "observed_run", run)
    result = preview.make_preview(src, out)
    cmd, options = calls[0]
    assert result["path"] == str(out) and result["size_bytes"] == 11
    assert out.read_bytes() == b"new preview" and not list(tmp_path.glob("*.part.mp4"))
    assert cmd[cmd.index("-i") + 1] == str(src) and cmd[cmd.index("-c:v") + 1] == "libx264"
    for flag, value in (
        ("-maxrate", "120k"),
        ("-bufsize", "240k"),
        ("-b:a", "48k"),
        ("-ac", "1"),
        ("-ar", "48000"),
        ("-pix_fmt", "yuv420p"),
        ("-movflags", "+faststart"),
    ):
        assert cmd[cmd.index(flag) + 1] == value
    assert "480" in cmd[cmd.index("-vf") + 1] and "min(" in cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*10)"
    assert cmd[cmd.index("-x264-params") + 1] == "vbv-init=0.25"
    assert options["check"] is True and options["capture_output"] is True
    assert cmd[:4] == ["nice", "-n", "10", "ffmpeg"]
    assert "preexec_fn" not in options


@pytest.mark.parametrize(
    "failure",
    [
        OSError("missing encoder"),
        subprocess.CalledProcessError(1, "ffmpeg"),
        subprocess.TimeoutExpired("ffmpeg", 1),
    ],
)
def test_failure_skips_without_losing_previous_output_or_leaving_parts(
    tmp_path, monkeypatch, failure
):
    src, out = paths(tmp_path)
    out.write_bytes(b"previous")

    def fail(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        raise failure

    monkeypatch.setattr(preview, "observed_run", fail)
    result = preview.make_preview(src, out)
    assert "skipped" in result and out.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.part.mp4"))


def test_empty_result_and_invalid_paths_skip(tmp_path, monkeypatch):
    src, out = paths(tmp_path)
    monkeypatch.setattr(preview, "observed_run", lambda *args, **kwargs: None)
    assert "skipped" in preview.make_preview(src, out)
    assert not out.exists() and not list(tmp_path.glob("*.part.mp4"))
    assert "skipped" in preview.make_preview(src, src)
    assert src.read_bytes() == b"synthetic blurred fixture"
    assert "skipped" in preview.make_preview(tmp_path / "missing.mp4", out)


def test_preview_stage_uses_blur_output_and_failure_never_blocks(tmp_path, monkeypatch):
    # Compile only this stage: no import-time production initialization or real-data access.
    path = Path(__file__).resolve().parents[1] / "reel/stages/__init__.py"
    tree = ast.parse(path.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "preview_stage"
    )
    namespace = {"Ctx": object}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    src = tmp_path / "final_blurred.mp4"
    ctx = SimpleNamespace(
        workdir=tmp_path,
        out=lambda name: {"final": str(src)}
        if name == "blur"
        else pytest.fail("unblurred input requested"),
    )
    calls = []
    monkeypatch.setattr(
        preview,
        "make_preview",
        lambda source, output: calls.append((source, output)) or {"path": str(output)},
    )
    assert namespace["preview_stage"](ctx)["path"] == str(tmp_path / "preview.mp4")
    assert calls == [(str(src), tmp_path / "preview.mp4")]
    monkeypatch.setattr(
        preview,
        "make_preview",
        lambda *args: (_ for _ in ()).throw(RuntimeError("fake encoder failure")),
    )
    assert "skipped" in namespace["preview_stage"](ctx)


def test_real_synthetic_preview_meets_size_audio_and_faststart_contract(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    src, out = tmp_path / "final_blurred.mp4", tmp_path / "preview.mp4"
    subprocess.run(
        [
            "nice",
            "-n",
            "10",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=960x540:rate=25:duration=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=10",
            "-ac",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    result = preview.make_preview(src, out)
    assert "skipped" not in result, result
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    )
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
    duration = float(probe["format"]["duration"])
    assert (
        video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p" and video["height"] <= 480
    )
    assert (
        audio["codec_name"] == "aac" and audio["channels"] == 1 and audio["sample_rate"] == "48000"
    )
    assert abs(duration - 10) <= 0.15
    assert out.stat().st_size / (duration / 60) / 1e6 <= 1.35
    data = out.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
