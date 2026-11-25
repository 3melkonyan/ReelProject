"""Cleanup checks use fake jobs or SQLite files exclusively under tmp_path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from reel.cleanup import _ReadOnlyDB, cleanup, main
from reel.db import DB

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def job(job_id=1, *, age=40, status="done", source="inbox/G/lesson.mp4"):
    return {"id": job_id, "status": status, "updated_at": (NOW - timedelta(days=age)).isoformat(),
            "source_path": source}


class FakeDB:
    def __init__(self, *rows, before_read=None):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.before_read = before_read
        self.reads = 0
        self.limit = None

    def jobs(self, limit=50):
        self.limit = limit
        rows = [dict(row) for row in self.rows.values()]
        return rows if limit < 0 else rows[:limit]

    def job(self, job_id):
        self.reads += 1
        if self.before_read:
            self.before_read(self, job_id)
        return dict(self.rows[job_id])


def put(root, name, data=b"temporary"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def names(root, report, field="candidates"):
    return {str(Path(path).relative_to(root)) for path in report[field]}


@pytest.mark.parametrize("status", ["queued", "running", "stopped"])
def test_only_done_jobs_can_be_cleaned(tmp_path, status):
    path = put(tmp_path, "work/1/final.mp4")
    archived = put(tmp_path, "archive/1/lesson.mp4")
    db = FakeDB(job(status=status, age=100))
    report = cleanup(db, tmp_path, archive_days=1, now=NOW)
    assert not report["candidates"] and db.reads == 0
    assert path.exists() and archived.exists()


def test_allowlist_and_permanent_preservation(tmp_path):
    allowed = {"audio16k.wav", "final.mp4", "encode.part.mp4", "nested/chunk.part001",
               "transcript/audio.wav", "transcript/pieces/voice.mp3", "transcript/voice.FLAC",
               "thumbnail.jpg.part", "preview.mp4.part", "final_blurred.mp4.part"}
    preserved = {"final_blurred.mp4", "thumbnail.jpg", "preview.mp4", "unknown.mp4", "unknown.wav",
                 "nested/final.mp4", "nested/audio16k.wav", "notes.txt", "transcript/transcript.txt",
                 "transcript/transcript.json", "blur_decisions.json", "metadata.JSON",
                 "transcript/transcript.json.part", "temp.JSONL.part", "privacy/sample.part.mp4",
                 "nested/privacy/audio.part.wav", "transcript/preview.mp4", "transcript/metadata.json.wav"}
    for name in allowed | preserved:
        put(tmp_path, f"work/1/{name}")
    db = FakeDB(job(age=10))
    report = cleanup(db, tmp_path, now=NOW)
    assert names(tmp_path, report, "deleted") == {f"work/1/{name}" for name in allowed}
    for name in preserved:
        assert (tmp_path / "work/1" / name).is_file(), name
    assert (tmp_path / "work/1/nested").is_dir()
    assert (tmp_path / "work/1/transcript/pieces").is_dir()
    assert (tmp_path / "work/1/privacy").is_dir()


def test_independent_retention_and_dry_run(tmp_path):
    for jid in range(1, 4):
        for name in ("final.mp4", "final_blurred.mp4"):
            put(tmp_path, f"work/{jid}/{name}")
    db = FakeDB(job(1, age=6), job(2, age=8), job(3, age=31))
    report = cleanup(db, tmp_path, dry_run=True, now=NOW)
    assert names(tmp_path, report) == {"work/2/final.mp4", "work/3/final.mp4", "work/3/final_blurred.mp4"}
    assert not report["deleted"] and report["bytes"] == 3 * len(b"temporary")
    assert len(list((tmp_path / "work").glob("*/*.mp4"))) == 6
    report = cleanup(db, tmp_path, days=60, blurred_days=30, now=NOW)
    assert names(tmp_path, report, "deleted") == {"work/3/final_blurred.mp4"}


@pytest.mark.parametrize("updated", ["invalid", "2020-01-01T00:00:00", None,
                                     (NOW + timedelta(days=1)).isoformat()])
def test_invalid_naive_or_future_timestamps_are_preserved(tmp_path, updated):
    path = put(tmp_path, "work/1/audio16k.wav")
    row = job()
    row["updated_at"] = updated
    assert not cleanup(FakeDB(row), tmp_path, now=NOW)["candidates"]
    assert path.exists()


def test_archive_requires_opt_in_and_uses_only_exact_id_and_basename(tmp_path):
    expected = put(tmp_path, "archive/1/lesson.mp4")
    other = put(tmp_path, "archive/1/other.mp4")
    duplicate = put(tmp_path, "archive/duplicates/lesson.mp4")
    other_job = put(tmp_path, "archive/2/lesson.mp4")
    source = put(tmp_path, "inbox/G/lesson.mp4")
    db = FakeDB(job(age=40, source=str(source)))
    assert not cleanup(db, tmp_path, now=NOW)["candidates"]
    assert not cleanup(db, tmp_path, archive_days=50, now=NOW)["candidates"]
    report = cleanup(db, tmp_path, archive_days=30, now=NOW)
    assert names(tmp_path, report, "deleted") == {"archive/1/lesson.mp4"}
    assert not expected.exists()
    assert all(path.exists() for path in (other, duplicate, other_job, source))
    assert expected.parent.is_dir()


@pytest.mark.parametrize("source", ["../lesson.mp4", "inbox/G/../../lesson.mp4", "/tmp/../lesson.mp4"])
def test_archive_rejects_source_traversal(tmp_path, source):
    archived = put(tmp_path, "archive/1/lesson.mp4")
    assert not cleanup(FakeDB(job(source=source)), tmp_path, archive_days=1, now=NOW)["candidates"]
    assert archived.exists()


def test_symlink_files_and_nested_directory_are_untouched(tmp_path):
    external = put(tmp_path, "outside/target.part.wav")
    work = tmp_path / "work/1"
    work.mkdir(parents=True)
    (work / "final.mp4").symlink_to(external)
    (work / "linked").symlink_to(external.parent, target_is_directory=True)
    (work / "transcript").symlink_to(external.parent, target_is_directory=True)
    archive = tmp_path / "archive/1"
    archive.mkdir(parents=True)
    (archive / "lesson.mp4").symlink_to(external)
    report = cleanup(FakeDB(job()), tmp_path, archive_days=1, now=NOW)
    assert not report["candidates"]
    assert external.read_bytes() == b"temporary"
    assert all((work / name).is_symlink() for name in ("final.mp4", "linked", "transcript"))
    assert (archive / "lesson.mp4").is_symlink()


@pytest.mark.parametrize("component", ["work", "work/1", "archive", "archive/1"])
def test_symlink_work_archive_or_job_ancestors_are_not_followed(tmp_path, component):
    target = tmp_path / "outside"
    target.mkdir()
    if component in {"work", "archive"}:
        put(target, "1/final.mp4")
        put(target, "1/lesson.mp4")
    else:
        put(target, "final.mp4")
        put(target, "lesson.mp4")
    link = tmp_path / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    assert not cleanup(FakeDB(job()), tmp_path, archive_days=1, now=NOW)["candidates"]
    assert link.is_symlink()
    assert len(list(target.rglob("*.mp4"))) == 2


def test_root_symlink_or_traversal_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        cleanup(FakeDB(), alias, now=NOW)
    with pytest.raises(ValueError):
        cleanup(FakeDB(), real / ".." / "real", now=NOW)


@pytest.mark.parametrize("change", ["status", "updated_at"])
def test_job_status_and_timestamp_are_rechecked_for_every_file(tmp_path, change):
    first = put(tmp_path, "work/1/audio16k.wav")
    second = put(tmp_path, "work/1/final.mp4")

    def change_on_second_read(db, jid):
        if db.reads == 2:
            db.rows[jid][change] = "running" if change == "status" else (NOW - timedelta(days=50)).isoformat()

    db = FakeDB(job(), before_read=change_on_second_read)
    report = cleanup(db, tmp_path, now=NOW)
    assert not first.exists() and second.exists()
    assert db.reads == 2 and len(report["deleted"]) == 1
    assert report["skipped"][0]["reason"] == "job changed"


@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_stale_file_fingerprint_is_rejected(tmp_path, replacement):
    candidate = put(tmp_path, "work/1/final.mp4")
    outside = put(tmp_path, "outside.mp4")

    def replace_during_recheck(db, jid):
        candidate.unlink()
        if replacement == "file":
            candidate.write_bytes(b"a newly created recording")
        else:
            candidate.symlink_to(outside)

    report = cleanup(FakeDB(job(), before_read=replace_during_recheck), tmp_path, now=NOW)
    assert not report["deleted"] and report["skipped"][0]["reason"] == "file changed"
    assert candidate.exists() and outside.read_bytes() == b"temporary"
    if replacement == "symlink":
        assert candidate.is_symlink()


def test_all_jobs_are_inspected_beyond_default_limit(tmp_path):
    db = FakeDB(*(job(jid) for jid in range(1, 62)))
    put(tmp_path, "work/61/final.mp4")
    report = cleanup(db, tmp_path, now=NOW)
    assert db.limit == -1
    assert names(tmp_path, report, "deleted") == {"work/61/final.mp4"}


def test_invalid_job_id_cannot_escape_its_directory(tmp_path):
    path = put(tmp_path, "outside/final.mp4")
    db = FakeDB(job("../outside"))
    assert not cleanup(db, tmp_path, now=NOW)["candidates"]
    assert path.exists()


def make_sqlite(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT, updated_at TEXT, source_path TEXT)")
        conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)",
                     (1, "done", (NOW - timedelta(days=40)).isoformat(), "inbox/G/lesson.mp4"))


def test_readonly_db_uses_db_reads_without_initialization_or_writes(tmp_path, monkeypatch):
    path = tmp_path / "read only?#.db"
    make_sqlite(path)
    before = path.read_bytes()
    before_files = set(tmp_path.iterdir())

    def forbidden_init(*args, **kwargs):
        raise AssertionError("DB.__init__ must not run")

    monkeypatch.setattr(DB, "__init__", forbidden_init)
    db = _ReadOnlyDB(path)
    try:
        assert db.jobs(limit=-1)[0]["id"] == 1
        assert db.job(1)["status"] == "done"
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        with pytest.raises(sqlite3.OperationalError):
            db.conn.execute("UPDATE jobs SET status='running'")
        with pytest.raises(sqlite3.OperationalError):
            db.conn.execute("CREATE TABLE unexpected (id INTEGER)")
    finally:
        db.conn.close()
    assert path.read_bytes() == before and set(tmp_path.iterdir()) == before_files


def test_readonly_db_missing_file_does_not_create_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises((OSError, sqlite3.OperationalError)):
        _ReadOnlyDB(path)
    assert not path.exists() and list(tmp_path.iterdir()) == []


def test_cli_dry_run_reads_only_temporary_database(tmp_path, capsys):
    path = tmp_path / "reel.db"
    make_sqlite(path)
    candidate = put(tmp_path, "work/1/final.mp4")
    before = path.read_bytes()
    assert main(["--root", str(tmp_path), "--days", "7", "--blurred-days", "30", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Would delete" in output and "GB" in output
    assert candidate.exists() and path.read_bytes() == before


@pytest.mark.parametrize("kwargs", [{"days": 0}, {"days": -1}, {"blurred_days": float("nan")},
                                    {"archive_days": float("inf")}, {"days": True}])
def test_invalid_retention_is_rejected_before_cleanup(tmp_path, kwargs):
    path = put(tmp_path, "work/1/final.mp4")
    with pytest.raises(ValueError):
        cleanup(FakeDB(job()), tmp_path, now=NOW, **kwargs)
    assert path.exists()


def test_readonly_db_observes_latest_status_committed_only_to_wal(tmp_path):
    path = tmp_path / "wal.db"
    make_sqlite(path)
    candidate = put(tmp_path, "work/1/final.mp4")
    writer = sqlite3.connect(path, isolation_level=None)
    reader = None
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE jobs SET status='running' WHERE id=1")
        wal = Path(str(path) + "-wal")
        assert wal.is_file()
        before_db, before_wal = path.read_bytes(), wal.read_bytes()
        # Establish that the main file alone still contains the old done state.
        frozen = sqlite3.connect(path.as_uri() + "?immutable=1", uri=True)
        try:
            assert frozen.execute("SELECT status FROM jobs WHERE id=1").fetchone()[0] == "done"
        finally:
            frozen.close()
        reader = _ReadOnlyDB(path)
        assert reader.jobs(limit=-1)[0]["status"] == "running"
        assert not cleanup(reader, tmp_path, now=NOW)["candidates"]
        assert candidate.exists() and path.read_bytes() == before_db
        assert wal.read_bytes() == before_wal
        writer.execute("UPDATE jobs SET status='queued' WHERE id=1")
        assert reader.job(1)["status"] == "queued"
    finally:
        if reader is not None:
            reader.conn.close()
        writer.close()


@pytest.mark.parametrize("symlink", ["file", "ancestor"])
def test_readonly_db_rejects_database_and_ancestor_symlinks(tmp_path, symlink):
    real = tmp_path / "real"
    real.mkdir()
    path = real / "reel.db"
    make_sqlite(path)
    before = path.read_bytes()
    if symlink == "file":
        alias = tmp_path / "alias.db"
        alias.symlink_to(path)
        target = alias
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)
        target = alias / "reel.db"
    with pytest.raises((ValueError, OSError)):
        _ReadOnlyDB(target)
    assert alias.is_symlink() and path.read_bytes() == before


def test_archive_source_change_after_planning_preserves_both_originals(tmp_path):
    original = put(tmp_path, "archive/1/old.mp4")
    replacement = put(tmp_path, "archive/1/new.mp4")

    def change_source(db, jid):
        db.rows[jid]["source_path"] = "inbox/G/new.mp4"

    db = FakeDB(job(source="inbox/G/old.mp4"), before_read=change_source)
    report = cleanup(db, tmp_path, archive_days=30, now=NOW)
    assert not report["deleted"] and report["skipped"][0]["reason"] == "job changed"
    assert original.exists() and replacement.exists()


@pytest.mark.parametrize("area, basename", [("work", "final.mp4"), ("archive", "lesson.mp4")])
def test_parent_replaced_by_symlink_during_job_recheck_is_skipped(tmp_path, area, basename):
    candidate = put(tmp_path, f"{area}/1/{basename}", b"original")
    outside = put(tmp_path, f"outside/{basename}", b"outside")
    original_parent = candidate.parent
    relocated = tmp_path / "relocated"

    def replace_parent(db, jid):
        original_parent.rename(relocated)
        original_parent.symlink_to(outside.parent, target_is_directory=True)

    db = FakeDB(job(), before_read=replace_parent)
    report = cleanup(db, tmp_path, archive_days=30, now=NOW)
    assert not report["deleted"] and len(report["skipped"]) == 1
    assert original_parent.is_symlink()
    assert (relocated / basename).read_bytes() == b"original"
    assert outside.read_bytes() == b"outside"
