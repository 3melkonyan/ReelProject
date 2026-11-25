"""Synthetic-only checks for the scoped blur performance profiling driver."""

import io
import json
import subprocess
import sys
import time

import numpy as np
import pytest

from tools import profile_blur as profile


def test_nested_timers_are_exclusive_and_phase_paths_separate_guard():
    now = [0.0]
    stats = profile.Measurements(wall=lambda: now[0], cpu=lambda: now[0] / 2)
    with stats.measure("phase.run"):
        with stats.measure("phase.analyze"):
            now[0] += 1
            with stats.measure("yunet"):
                now[0] += 2
            now[0] += 1
        with stats.measure("phase.privacy"), stats.measure("phase.analyze"), stats.measure("yunet"):
            now[0] += 3
    rows = {(r["phase"], r["metric"]): r for r in stats.report()["metrics"]}
    analysis = rows["run/analyze", "phase.analyze"]
    assert analysis["wall_inclusive_sec"] == 4
    assert analysis["wall_exclusive_sec"] == 2
    assert analysis["parent_cpu_exclusive_sec"] == 1
    assert rows["run/analyze", "yunet"]["exclusive_percent_of_phase"] == 50
    assert rows["run/privacy/analyze", "yunet"]["wall_inclusive_sec"] == 3
    assert stats.stack() == []


def test_timer_records_failure_and_preserves_exception():
    stats = profile.Measurements()
    with pytest.raises(ValueError, match="synthetic"), stats.measure("phase.analyze"):
        raise ValueError("synthetic")
    assert stats.stack() == []
    assert stats.report()["metrics"][0]["calls"] == 1


def test_pipe_delegates_buffer_io_and_close():
    stats = profile.Measurements()
    original = io.BytesIO(b"abc")
    pipe = profile.Pipe(original, stats, "decode")
    target = bytearray(3)
    assert pipe.readinto(target) == 3
    assert target == b"abc"
    assert pipe.read(1) == b""
    pipe.close()
    assert original.closed
    names = {r["metric"] for r in stats.report()["metrics"]}
    assert names == {
        "ffmpeg.decode.pipe_readinto",
        "ffmpeg.decode.pipe_read",
        "ffmpeg.decode.pipe_close",
    }


@pytest.mark.parametrize("poll_first", [False, True])
def test_popen_collects_child_cpu_once_for_wait_and_poll(poll_first):
    stats = profile.Measurements()
    profiled = profile.popen_class(subprocess.Popen, stats)
    with stats.measure("phase.analyze"):
        child = profiled([sys.executable, "-c", "sum(range(1000))"], stdout=subprocess.PIPE)
        if poll_first:
            deadline = time.monotonic() + 10
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.001)
            assert child.poll() == 0
            child.stdout.close()
        else:
            child.communicate(timeout=10)
        assert child.wait(timeout=10) == 0
        assert child.wait(timeout=10) == 0
    assert len(stats.children) == 1
    result = stats.children[0]
    assert result["phase"] == "analyze"
    assert result["role"] == "probe"
    assert result["cpu_sec"] >= 0
    assert result["lifetime_wall_sec"] > 0


def test_instrumentation_restores_every_patch_on_failure(monkeypatch):
    import cv2

    from reel import blur, lecturer
    from reel.stages import privacy

    class FakeFaceID:
        def __init__(self, refs):
            self.refs = refs

        def faces(self, image):
            return np.zeros((0, 15), np.float32)

        def feature(self, image, face):
            return np.ones((1, 128), np.float32)

        def best_match(self, feature):
            return 0.5

    for module in (blur, lecturer, privacy):
        monkeypatch.setattr(module, "FaceID", FakeFaceID)
    originals = (
        subprocess.Popen,
        cv2.resize,
        blur.analyze,
        blur._Tracker.update,
        lecturer.discover,
    )
    stats = profile.Measurements()
    with (
        pytest.raises(ValueError, match="synthetic"),
        profile.instrument(stats),
        stats.measure("phase.analyze"),
    ):
        assert blur.FaceID is lecturer.FaceID is privacy.FaceID
        fid = blur.FaceID([])
        image = np.zeros((2, 2, 3), np.uint8)
        assert fid.faces(image).shape == (0, 15)
        assert fid.best_match(fid.feature(image, [])) == 0.5
        assert cv2.resize(image, (1, 1)).shape == (1, 1, 3)
        raise ValueError("synthetic")
    assert originals == (
        subprocess.Popen,
        cv2.resize,
        blur.analyze,
        blur._Tracker.update,
        lecturer.discover,
    )
    assert blur.FaceID is lecturer.FaceID is privacy.FaceID is FakeFaceID
    names = {r["metric"] for r in stats.report()["metrics"]}
    assert {"faceid.load", "yunet", "sface.feature_align", "sface.match", "resize"} <= names


def test_output_rejects_outside_directory_and_resolves_symlinks(tmp_path):
    root = tmp_path / "out/performance"
    root.mkdir(parents=True)
    assert profile.safe_output(root / "baseline", root) == root / "baseline"
    with pytest.raises(ValueError, match="out/performance"):
        profile.safe_output(root / "../escape", root)
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="out/performance"):
        profile.safe_output(root / "link/private", root)


def test_analysis_export_never_serializes_biometric_features():
    records = [{"t": 0.0, "box": [0, 0, 16, 16], "feat": np.ones((1, 128))}]
    assert profile.serializable_analysis(records) == [{"t": 0.0, "box": [0, 0, 16, 16]}]
    assert "feat" in records[0]


def test_default_output_rejects_redirected_out_directory(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "out").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(profile, "ROOT", root)
    monkeypatch.setattr(profile, "OUT", root / "out/performance")
    with pytest.raises(ValueError):
        profile.safe_output(root / "out/performance/reference")


def test_cli_refuses_stale_outputs_before_loading_models(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    monkeypatch.setattr(profile, "OUT", tmp_path / "out/performance")
    folder = profile.OUT / "reference"
    folder.mkdir(parents=True)
    saved = folder / "lecturer_choice.json"
    saved.write_text('{"none": true}')
    with pytest.raises(SystemExit) as exc:
        profile.main(
            ["stage", "--video", str(tmp_path / "synthetic.mp4"), "--out-dir", str(folder)]
        )
    assert exc.value.code == 2
    assert saved.read_text() == '{"none": true}'


def test_interrupted_run_is_reported_as_incomplete(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from reel import blur

    monkeypatch.setattr(profile, "ROOT", tmp_path)
    monkeypatch.setattr(profile, "OUT", tmp_path / "out/performance")
    out = profile.OUT / "interrupted"
    out.mkdir(parents=True)
    monkeypatch.setattr(profile, "instrument", lambda stats: nullcontext())

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(blur, "analyze", interrupted)
    args = SimpleNamespace(
        mode="analyze",
        video=tmp_path / "synthetic.mp4",
        job_id=1,
        fps=5,
        label="interrupted",
        opencv_threads=None,
        refs=[],
        with_privacy=False,
    )
    with pytest.raises(KeyboardInterrupt):
        profile.run(args, out)
    report = json.loads((out / "profile.json").read_text())
    assert report["error"]["type"] == "KeyboardInterrupt"
    assert "privacy" not in report and "blur" not in report


def test_background_pipe_metrics_keep_the_creating_phase():
    from threading import Thread

    stats = profile.Measurements()
    with stats.measure("phase.analyze"):
        pipe = profile.Pipe(io.BytesIO(b"data"), stats, "decode")
        thread = Thread(target=lambda: pipe.read(4))
        thread.start()
        thread.join()
    rows = stats.report()["metrics"]
    row = next(r for r in rows if r["metric"] == "ffmpeg.decode.pipe_read")
    assert row["phase"] == "analyze"


def test_progress_events_describe_phase_boundaries():
    events = []
    stats = profile.Measurements(progress=events.append)
    with stats.measure("phase.run"), stats.measure("identify"):
        pass
    assert [event["event"] for event in events] == ["started", "finished"]
    assert all(event["phase"] == "run" for event in events)
    assert events[-1]["wall_sec"] >= 0
