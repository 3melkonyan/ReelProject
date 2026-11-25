"""Pipeline guarantees: resume, retry, stop, idempotent publish, inbox dedup."""
from __future__ import annotations

from pathlib import Path

import pytest

from reel import pipeline
from reel.db import DB
from reel.pipeline import StopJob, run_job

CFG = {"groups": {"G": {}}}


class NonRetryable(Exception):
    retryable = False


def mk(tmp_path: Path) -> tuple[DB, int]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = DB(tmp_path / "t.db")
    src = tmp_path / "x.mp4"; src.write_bytes(b"v")
    return db, db.create_job(str(src), "sha", "G")


def run(db, jid, stages, tmp_path, notes=None):
    return run_job(db, jid, stages, CFG, tmp_path,
                   notify=(notes.append if notes is not None else lambda t: None),
                   sleep=lambda s: None)


def test_resume_skips_done_stages(tmp_path):
    db, jid = mk(tmp_path)
    calls = []
    crash = {"on": True}

    def a(ctx): calls.append("a"); return {"v": 1}
    def b(ctx):
        calls.append("b")
        if crash["on"]:
            raise KeyboardInterrupt  # simulates kill -9 mid-stage
        return {"got": ctx.out("a")["v"]}

    with pytest.raises(KeyboardInterrupt):
        run(db, jid, [("a", a), ("b", b)], tmp_path)
    assert db.next_runnable()["id"] == jid          # left 'running' -> resumed first
    crash["on"] = False
    assert run(db, jid, [("a", a), ("b", b)], tmp_path) == "done"
    assert calls == ["a", "b", "b"]
    assert db.stage_output(jid, "b") == {"got": 1}  # output of 'a' read back from DB


def test_retry_then_stop_and_manual_retry(tmp_path):
    db, jid = mk(tmp_path)
    n = {"c": 0}

    def flaky(ctx):
        n["c"] += 1
        raise RuntimeError("network")

    notes = []
    assert run(db, jid, [("f", flaky)], tmp_path, notes) == "stopped"
    assert n["c"] == pipeline.MAX_ATTEMPTS
    assert "stopped" in notes[-1] and "reel retry" in notes[-1]
    pipeline.retry(db, jid)
    assert db.job(jid)["status"] == "queued"


def test_stopjob_and_nonretryable_stop_immediately(tmp_path):
    for exc in (StopJob("uncertain"), NonRetryable("bad auth")):
        db, jid = mk(tmp_path / type(exc).__name__)
        n = {"c": 0}

        def s(ctx, exc=exc, n=n):
            n["c"] += 1
            raise exc

        assert run(db, jid, [("s", s)], tmp_path) == "stopped"
        assert n["c"] == 1
        assert db.stage(jid, "s")["status"] == "failed"


def test_publish_once_no_duplicates(tmp_path):
    db, jid = mk(tmp_path)
    remote = []

    def upload():
        remote.append("vid"); return ("vid1", "https://youtu.be/vid1")

    def find():
        return ("vid1", "https://youtu.be/vid1") if remote else None

    def stage(ctx):
        r = ctx.publish_once("youtube", upload, find)
        if not ctx.out("_crashed"):
            ctx._outputs["_crashed"] = {"x": 1}
            # simulate crash AFTER remote upload but BEFORE we learn it succeeded
            db.conn.execute("DELETE FROM publications")
            raise RuntimeError("crash after upload")
        return r

    assert run(db, jid, [("yt", stage)], tmp_path) == "done"
    assert remote == ["vid"]                          # uploaded exactly once
    assert db.publication(jid, "youtube")["external_id"] == "vid1"


def test_inbox_dedup_and_stability(tmp_path, monkeypatch):
    db = DB(tmp_path / "t.db")
    (tmp_path / "inbox" / "G").mkdir(parents=True)
    f = tmp_path / "inbox" / "G" / "lesson.mp4"; f.write_bytes(b"data")
    clock = [1000.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    seen: dict = {}
    assert pipeline.scan_inbox(db, tmp_path, CFG, lambda t: None, seen) == []  # first sight
    clock[0] += pipeline.STABLE_SEC + 1
    [jid] = pipeline.scan_inbox(db, tmp_path, CFG, lambda t: None, seen)
    archived = Path(db.job(jid)["source_path"])
    assert archived.read_bytes() == b"data" and not f.exists()
    # same content again -> no new job
    f.write_bytes(b"data")
    notes = []
    pipeline.scan_inbox(db, tmp_path, CFG, notes.append, seen)
    clock[0] += pipeline.STABLE_SEC + 1
    assert pipeline.scan_inbox(db, tmp_path, CFG, notes.append, seen) == []
    assert "already processed" in notes[-1]


def test_unqueryable_target_stops_instead_of_double_post(tmp_path):
    db, jid = mk(tmp_path)
    posts = []

    def stage(ctx):
        def do():
            posts.append(1)
            raise RuntimeError("crash right after posting")   # posted, but we never learn it
        return ctx.publish_once("slack", do)

    notes = []
    assert run(db, jid, [("slack", stage)], tmp_path, notes) == "stopped"
    assert posts == [1]                                  # second attempt did NOT post again
    assert "reel resolve" in notes[-1]


def test_stage_modules_not_shadowed_by_stage_functions():
    """Regression: a stage function named like its module made
    `from reel.stages import segments` return the function, not the module."""
    import subprocess
    import sys
    code = ("import types; from reel.stages import segments, render, privacy, metadata, transcribe; "
            "assert all(isinstance(m, types.ModuleType) for m in "
            "(segments, render, privacy, metadata, transcribe))")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[1])
