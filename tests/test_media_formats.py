"""Short real-media G-23 matrix; all generated inputs and outputs live in pytest temp dirs.

No detector, model, database, original lesson, worker or service is started here.
The shared 2-second fixtures keep the complete hardware-assisted matrix small.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path

import pytest

from reel import blur
from reel.stages import join as joiner
from reel.stages import render as renderer

CASES = [
    pytest.param("a_hevc_hlg_4k", (1920, 1080), False, id="4k-hevc10-hlg-mov"),
    pytest.param("b_rotated_hevc", (1080, 1920), False, id="4k-hevc10-portrait-matrix"),
    pytest.param("c_h264_1080", (1920, 1080), True, id="1080p-h264-compatible"),
    pytest.param("d_h264_720", (1280, 720), True, id="720p-no-upscale"),
    pytest.param("e_h264_1440_60", (1920, 1080), False, id="1440p-h264-60fps"),
    pytest.param("f_mov_data", (1920, 1080), True, id="h264-mov-timecode-data"),
]


def run(args, *, timeout=30):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]).stdout)


def video_stream(info):
    return next(stream for stream in info["streams"] if stream["codec_type"] == "video")


def audio_stream(info):
    return next(stream for stream in info["streams"] if stream["codec_type"] == "audio")


def duration(path):
    return float(probe(path)["format"]["duration"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_args(size, fps=30):
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-filter_threads", "2", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-t", "2"]


@pytest.fixture(scope="module", autouse=True)
def media_runtime_budget():
    """Bound actual ffmpeg/ffprobe work, including render/join's inner calls."""
    original_run = subprocess.run
    started = time.monotonic()
    deadline = started + 88.5
    patch = pytest.MonkeyPatch()

    def bounded_run(*args, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail("G-23 media module exhausted its shared 88.5-second subprocess budget")
        explicit = kwargs.get("timeout")
        kwargs["timeout"] = min(explicit, remaining) if explicit is not None else remaining
        try:
            return original_run(*args, **kwargs)
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                pytest.fail("G-23 media module exhausted its shared 88.5-second subprocess budget")
            raise

    patch.setattr(subprocess, "run", bounded_run)
    try:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            pytest.skip("real media tests require ffmpeg and ffprobe")
        encoders = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        if "hevc_videotoolbox" not in encoders:
            pytest.skip("G-23 hardware media matrix requires HEVC VideoToolbox")
        yield encoders
    finally:
        patch.undo()
        elapsed = time.monotonic() - started
        assert elapsed < 90, f"G-23 media module exceeded 90 seconds: {elapsed:.2f}s"


@pytest.fixture(scope="module")
def media(tmp_path_factory, media_runtime_budget):
    root = tmp_path_factory.mktemp("g23_media_formats")
    encoders = media_runtime_budget
    paths, hevc_unavailable = {}, []

    def make_h264(name, size, fps=30):
        path = root / f"{name}.mp4"
        common = _fixture_args(size, fps) + ["-pix_fmt", "yuv420p"]
        audio = ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
        if "h264_videotoolbox" in encoders:
            try:
                run(common + ["-c:v", "h264_videotoolbox", "-b:v", "4M", *audio, str(path)])
                return path
            except subprocess.CalledProcessError:
                path.unlink(missing_ok=True)
        run(common + ["-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-crf", "24",
                      *audio, str(path)])
        return path

    def get(name):
        if name in paths:
            return paths[name]
        if name == "a_hevc_hlg_4k":
            if hevc_unavailable:
                pytest.skip(hevc_unavailable[0])
            if "hevc_videotoolbox" not in encoders:
                hevc_unavailable.append("HEVC VideoToolbox encoder is unavailable")
                pytest.skip(hevc_unavailable[0])
            path = root / "a_hevc_hlg_4k.mov"
            try:
                run(_fixture_args("3840x2160") + [
                    "-vf", "format=p010le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
                    "-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-pix_fmt", "p010le", "-b:v", "8M",
                    "-tag:v", "hvc1", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
                    "-colorspace", "bt2020nc", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
                    "-movflags", "+write_colr", str(path)])
            except subprocess.CalledProcessError as exc:
                hevc_unavailable.append("HEVC VideoToolbox fixture encoding unavailable: " + exc.stderr[-350:])
                pytest.skip(hevc_unavailable[0])
            info = probe(path)
            video = video_stream(info)
            assert (video["codec_name"], video["width"], video["height"]) == ("hevc", 3840, 2160)
            assert video["pix_fmt"] in ("yuv420p10le", "p010le")
            assert (video["color_primaries"], video["color_transfer"], video["color_space"]) == (
                "bt2020", "arib-std-b67", "bt2020nc")
            assert audio_stream(info)["channels"] == 2
        elif name == "b_rotated_hevc":
            source = get("a_hevc_hlg_4k")
            path = root / "b_rotated_hevc.mov"
            run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-display_rotation:v:0", "90", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
                 "-c", "copy", "-movflags", "+write_colr", str(path)])
            video = video_stream(probe(path))
            assert (video["width"], video["height"]) == (3840, 2160)
            assert abs(blur._rotation(video)) == 90  # Physically landscape, displayed portrait.
        elif name == "c_h264_1080":
            path = make_h264(name, "1920x1080")
        elif name == "d_h264_720":
            path = make_h264(name, "1280x720")
        elif name == "e_h264_1440_60":
            path = make_h264(name, "2560x1440", 60)
            assert Fraction(video_stream(probe(path))["r_frame_rate"]) == 60
        elif name == "f_mov_data":
            path = root / "f_h264_timecode.mov"
            source = get("c_h264_1080")
            try:
                run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                     "-timecode", "01:02:03:00", "-write_tmcd", "1", str(path)])
            except subprocess.CalledProcessError as exc:
                pytest.skip("optional MOV timecode data mux unsupported: " + exc.stderr[-350:])
            assert any(stream["codec_type"] == "data" for stream in probe(path)["streams"])
        else:
            raise AssertionError(f"unknown fixture: {name}")
        paths[name] = path
        return path

    # A runtime-unavailable HEVC device also skips this module as one hardware matrix.
    get("a_hevc_hlg_4k")
    return SimpleMedia(root, get)


class SimpleMedia:
    def __init__(self, root, get):
        self.root, self.get = root, get
        self.joined = None

    def joined_hevc(self):
        if self.joined is None:
            source = self.get("a_hevc_hlg_4k")
            folder = self.root / "joined"
            folder.mkdir()
            parts = [folder / "part1.mov", folder / "part2.mov"]
            for path in parts:
                shutil.copyfile(source, path)
            expected = sum(duration(path) for path in parts)
            hashes = [digest(path) for path in parts]
            result = joiner.join(parts, folder)
            assert result["mode"] == "copy" and result["parts"] == 2
            path = Path(result["file"])
            assert abs(duration(path) - expected) <= 0.15
            assert [digest(part) for part in parts] == hashes
            assert video_stream(probe(path))["codec_name"] == "hevc"
            self.joined = path
        return self.joined


def assert_output_contract(source, output, expected_size, expected_duration, mode):
    info, original = probe(output), video_stream(probe(source))
    video, audio = video_stream(info), audio_stream(info)
    width, height = video["width"], video["height"]
    actual_duration = float(info["format"]["duration"])
    sar, dar = video.get("sample_aspect_ratio", "1:1"), video.get("display_aspect_ratio")
    details = json.dumps({"source": source.name, "mode": mode, "codec": video["codec_name"],
               "pix_fmt": video["pix_fmt"], "dimensions": (width, height), "rotation": blur._rotation(video),
               "sar": sar, "dar": dar,
               "audio": (audio["codec_name"], audio["sample_rate"]), "duration": actual_duration,
               "expected_dimensions": expected_size, "expected_duration": expected_duration}, separators=(",", ":"))
    assert (width, height) == expected_size, details
    assert width % 2 == height % 2 == 0 and min(width, height) <= 1080, details
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p", details
    # Ա1 (render review F1): HDR (HLG/PQ, BT.2020) must leave render converted to SDR BT.709
    assert video.get("color_transfer") in (None, "unknown", "bt709"), details
    assert video.get("color_primaries") in (None, "unknown", "bt709"), details
    assert math.isclose(blur._rotation(video), 0, abs_tol=0.01), details
    assert Fraction(sar.replace(":", "/")) == 1, details
    if dar not in (None, "N/A", "0:1"):
        display_ratio = Fraction(dar.replace(":", "/"))
        assert abs(float(display_ratio) * expected_size[1] - expected_size[0]) <= 2, details
    displayed_width, displayed_height = original["width"], original["height"]
    if round(blur._rotation(original) / 90) % 2:
        displayed_width, displayed_height = displayed_height, displayed_width
    scale = min(1, 1080 / min(displayed_width, displayed_height))
    assert abs(width - displayed_width * scale) <= 2, details
    assert abs(height - displayed_height * scale) <= 2, details
    assert width <= displayed_width and height <= displayed_height, details
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == 48000, details
    assert abs(actual_duration - expected_duration) <= 0.15, details
    for stream in (video, audio):
        if stream.get("duration") is not None:
            assert abs(float(stream["duration"]) - expected_duration) <= 0.15, details
    geometry = blur._geometry(blur._probe(output))
    assert geometry[:2] == (width, height) and 0 < geometry[2] <= 1000, details
    assert not list(output.parent.glob("*.part.mp4")), details
    return details


def render_case(source, output, *, cut, expected_size, compatible):
    total = duration(source)
    keep = [(0, 0.8), (1.2, total)] if cut else [(0, total)]
    before = digest(source)
    try:
        result = renderer.render(source, keep, output)
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        pytest.fail(f"render failed for {source.name}, cuts={cut}: {error}")
    assert result["final"] == str(output)
    assert digest(source) == before
    details = assert_output_contract(source, output, expected_size, sum(end - start for start, end in keep), result["mode"])
    if compatible and not cut:
        assert result["mode"] == "copy", details
    else:
        assert result["mode"] in ("reencode_hw", "reencode"), details


@pytest.mark.parametrize("name,expected_size,compatible", CASES)
@pytest.mark.parametrize("cut", [False, True], ids=["full", "cuts"])
def test_render_normalizes_media_format_matrix(media, tmp_path, name, expected_size, compatible, cut):
    source = media.get(name)
    render_case(source, tmp_path / "normalized.mp4", cut=cut, expected_size=expected_size, compatible=compatible)


@pytest.mark.parametrize("cut", [False, True], ids=["full", "cuts"])
def test_stream_copied_hevc_parts_are_normalized_after_join(media, tmp_path, cut):
    source = media.joined_hevc()
    render_case(source, tmp_path / "joined_normalized.mp4", cut=cut, expected_size=(1920, 1080), compatible=False)
