"""Lessons recorded in parts: a folder in the inbox -> one job -> one joined video file."""
import subprocess
from pathlib import Path

import pytest

from reel import pipeline
from reel.db import DB
from reel.hints import hint
from reel.pipeline import StopJob, parts_of, run_job
from reel.stages import join as jn
from reel.stages import join_stage

CFG = {"groups": {"G": {}}}


def clip(path: Path, sec: float, size: str = "320x240") -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(sec),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


def test_parts_sorted_in_recording_order(tmp_path):
    for name in ("dasi_10.mp4", "dasi_2.mp4", "Dasi_1.mp4", "notes.txt", ".hidden.mp4", "3.mov"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in parts_of(tmp_path)] == ["3.mov", "Dasi_1.mp4", "dasi_2.mp4", "dasi_10.mp4"]


def test_folder_is_one_job_once_it_stops_changing(tmp_path, monkeypatch):
    db = DB(tmp_path / "t.db")
    lesson = tmp_path / "inbox" / "G" / "lesson-nov18"
    lesson.mkdir(parents=True)
    (lesson / "part1.mp4").write_bytes(b"one")
    clock = [1000.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    seen, notes = {}, []

    def scan():
        return pipeline.scan_inbox(db, tmp_path, CFG, notes.append, seen)

    assert scan() == []                                       # first sight
    clock[0] += pipeline.FOLDER_STABLE_SEC + 1
    (lesson / "part2.mp4").write_bytes(b"two")                # part 2 still arriving: timer restarts
    assert scan() == []
    clock[0] += pipeline.STABLE_SEC + 1
    assert scan() == []                                       # a single file would go now; a folder waits
    clock[0] += pipeline.FOLDER_STABLE_SEC
    [jid] = scan()
    src = Path(db.job(jid)["source_path"])
    assert src.is_dir() and src.name == "lesson-nov18" and not lesson.exists()
    assert [p.name for p in parts_of(src)] == ["part1.mp4", "part2.mp4"]
    again = tmp_path / "inbox" / "G" / "lesson-copy"          # the same parts again -> duplicate
    again.mkdir()
    (again / "a1.mp4").write_bytes(b"one")
    (again / "a2.mp4").write_bytes(b"two")
    scan()
    clock[0] += pipeline.FOLDER_STABLE_SEC + 1
    assert scan() == [] and "already processed" in notes[-1]
    assert (tmp_path / "archive" / "duplicates" / "lesson-copy").is_dir()


def test_join_by_stream_copy_keeps_full_length(tmp_path):
    r = jn.join([clip(tmp_path / "p1.mp4", 2.0), clip(tmp_path / "p2.mp4", 3.0)], tmp_path)
    assert r["mode"] == "copy" and r["parts"] == 2 and abs(r["joined_sec"] - 5.0) < 0.3
    assert Path(r["file"]).name == "source_joined.mp4" and not list(tmp_path.glob("source_joined.part*"))


def test_join_different_sizes_reencodes(tmp_path):
    r = jn.join([clip(tmp_path / "p1.mp4", 2.0, "320x240"), clip(tmp_path / "p2.mp4", 2.0, "640x360")], tmp_path)
    assert r["mode"] in ("reencode_hw", "reencode") and abs(r["joined_sec"] - 4.0) < 0.3


def test_unreadable_part_stops_with_its_name(tmp_path):
    bad = tmp_path / "p2.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(StopJob, match="p2.mp4"):
        jn.join([clip(tmp_path / "p1.mp4", 1.0), bad], tmp_path)
    assert "մասեր" in hint("[join] cannot join 2 parts")


def test_later_stages_get_the_joined_file(tmp_path):
    db = DB(tmp_path / "t.db")
    folder = tmp_path / "archive" / "1" / "lesson"
    folder.mkdir(parents=True)
    clip(folder / "1.mp4", 1.5)
    clip(folder / "2.mp4", 1.5)
    jid = db.create_job(str(folder), "digest", "G")
    got = {}

    def probe_like(ctx):
        got["media"] = ctx.media()
        return {}

    stages = [("join", join_stage), ("probe_like", probe_like)]
    assert run_job(db, jid, stages, CFG, tmp_path, notify=lambda t: None, sleep=lambda s: None) == "done"
    assert got["media"] == tmp_path / "work" / str(jid) / "source_joined.mp4" and got["media"].is_file()


def test_single_file_passes_through(tmp_path):
    db = DB(tmp_path / "t.db")
    f = clip(tmp_path / "lesson.mp4", 1.0)
    jid = db.create_job(str(f), "digest", "G")
    got = {}

    def probe_like(ctx):
        got["media"] = ctx.media()
        return {}

    assert run_job(db, jid, [("join", join_stage), ("p", probe_like)], CFG, tmp_path,
                   notify=lambda t: None, sleep=lambda s: None) == "done"
    assert got["media"] == f and db.stage_output(jid, "join") == {"parts": 1}


def test_dashboard_lists_a_waiting_lesson_folder(tmp_path, monkeypatch):
    from reel.web import app
    lesson = tmp_path / "inbox" / "G" / "lesson-nov18"
    lesson.mkdir(parents=True)
    (lesson / "1.mp4").write_bytes(b"x" * 1000)
    (lesson / "2.mp4").write_bytes(b"y" * 1000)
    (tmp_path / "inbox" / "G" / "single.mp4").write_bytes(b"z")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    items = {i["file"]: i for i in app._inbox()}
    assert items["lesson-nov18"]["parts"] == 2 and items["single.mp4"]["parts"] == 1
