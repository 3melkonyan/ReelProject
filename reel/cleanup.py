"""Remove allowlisted intermediates belonging to completed, old jobs.

Run ``python -m reel.cleanup --days 7 --blurred-days 30 --dry-run`` to
inspect candidates. Archived originals are retained unless --archive-days is
explicitly supplied. Database rows and directories are never changed.
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reel.db import DB

ROOT = Path(__file__).resolve().parents[1]
_AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff", ".wma"}
_PROTECTED = ("thumbnail.jpg", "preview.mp4", "final_blurred.mp4")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _absolute(path: str | Path) -> Path:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise ValueError("Paths containing '..' are not allowed")
    return path.absolute()


@contextmanager
def _directory(root: Path, parts: tuple[str, ...] = ()):
    """Open every ancestor without following symlinks, including root itself."""
    fd = os.open(root.anchor, _DIR_FLAGS)
    try:
        for name in (*root.parts[1:], *parts):
            if name in {"", ".", ".."} or "/" in name:
                raise ValueError("Unsafe path component")
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


class _ReadOnlyDB(DB):
    """DB's read interface without its schema or journal initialization."""

    def __init__(self, path: str | Path = "reel.db"):
        target = _absolute(path)
        with _directory(target.parent) as parent:
            info = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Database must be a regular, nonsymlink file")
        self.conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")


def _days(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def _old_enough(updated_at: object, now: datetime, days: float) -> bool:
    if not isinstance(updated_at, str):
        return False
    try:
        updated = datetime.fromisoformat(updated_at)
        return updated.utcoffset() is not None and now - updated >= timedelta(days=days)
    except (ValueError, TypeError, OverflowError):
        return False


def _job_id(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _protected(parts: tuple[str, ...]) -> bool:
    name = parts[-1].casefold()
    return (
        any(part.casefold() == "privacy" for part in parts[:-1])
        or ".json" in name
        or name in _PROTECTED
    )


def _walk(fd: int, parts: tuple[str, ...]):
    """Yield regular files only; no directory or symlink can be a candidate."""
    for name in sorted(os.listdir(fd)):
        child_parts = (*parts, name)
        if name.casefold() == "privacy":
            continue
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                yield child_parts, info
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                try:
                    yield from _walk(child, child_parts)
                finally:
                    os.close(child)
        except OSError:
            continue


@dataclass(frozen=True)
class _Candidate:
    job_id: int
    updated_at: str
    parts: tuple[str, ...]
    days: float
    fingerprint: tuple[int, ...]
    source_path: str | None = None


def _work_candidates(root: Path, job: dict, days: float, blurred_days: float, now: datetime):
    try:
        with _directory(root, ("work", str(job["id"]))) as fd:
            for relative, info in _walk(fd, ()):
                if relative == ("final_blurred.mp4",):
                    retention = blurred_days
                elif _protected(relative):
                    continue
                elif (
                    relative in {("audio16k.wav",), ("final.mp4",)}
                    or (len(relative) == 1 and relative[0].casefold().startswith("source_joined."))
                    or ".part" in relative[-1].casefold()
                    or (
                        relative[0] == "transcript"
                        and len(relative) > 1
                        and Path(relative[-1]).suffix.casefold() in _AUDIO
                    )
                ):
                    retention = days
                else:
                    continue
                if _old_enough(job["updated_at"], now, retention):
                    yield _Candidate(
                        job["id"],
                        job["updated_at"],
                        ("work", str(job["id"]), *relative),
                        retention,
                        _fingerprint(info),
                    )
    except OSError:
        return


def _archive_candidate(root: Path, job: dict, archive_days: float, now: datetime):
    if not _old_enough(job["updated_at"], now, archive_days):
        return
    source = job.get("source_path")
    if not isinstance(source, str) or not source or "\x00" in source:
        return
    path = Path(source)
    if ".." in path.parts or not path.name or _protected((path.name,)):
        return
    # The DB may still point at inbox; only its original basename is used.
    parts = ("archive", str(job["id"]), path.name)
    try:
        with _directory(root, parts[:-1]) as fd:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                yield _Candidate(
                    job["id"], job["updated_at"], parts, archive_days, _fingerprint(info), source
                )
    except OSError:
        return


def cleanup(
    db: DB,
    root: str | Path,
    *,
    days: float = 7,
    blurred_days: float = 30,
    archive_days: float | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Clean only allowlisted files; revalidate job state and file identity.

    ``db`` needs only jobs(limit=-1) and job(id), allowing isolated fake DBs.
    A job must remain done with its original updated_at for every candidate.
    Returns candidate/deleted paths, skipped reasons, and eligible byte count.
    """
    days, blurred_days = _days(days, "days"), _days(blurred_days, "blurred_days")
    if archive_days is not None:
        archive_days = _days(archive_days, "archive_days")
    now = now if now is not None else datetime.now(UTC)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    root = _absolute(root)
    with _directory(root):
        pass
    planned = []
    for row in db.jobs(limit=-1):
        job = dict(row)
        if (
            _job_id(job.get("id")) is None
            or job.get("status") != "done"
            or not isinstance(job.get("updated_at"), str)
        ):
            continue
        planned.extend(_work_candidates(root, job, days, blurred_days, now))
        if archive_days is not None:
            planned.extend(_archive_candidate(root, job, archive_days, now))

    report = {"dry_run": bool(dry_run), "candidates": [], "deleted": [], "skipped": [], "bytes": 0}
    for candidate in planned:
        path = str(root.joinpath(*candidate.parts))
        reason = None
        try:
            with _directory(root, candidate.parts[:-1]) as fd:
                parent_info = os.fstat(fd)
                info = os.stat(candidate.parts[-1], dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or _fingerprint(info) != candidate.fingerprint:
                    reason = "file changed"
                else:
                    try:
                        job = dict(db.job(candidate.job_id))
                    except KeyError:
                        job = {}
                    if (
                        job.get("status") != "done"
                        or job.get("updated_at") != candidate.updated_at
                        or not _old_enough(job.get("updated_at"), now, candidate.days)
                        or (
                            candidate.source_path is not None
                            and job.get("source_path") != candidate.source_path
                        )
                    ):
                        reason = "job changed"
                    else:
                        # Reopen from root: the old descriptor may now refer to
                        # a directory relocated while the job was rechecked.
                        with _directory(root, candidate.parts[:-1]) as current_fd:
                            if not os.path.samestat(parent_info, os.fstat(current_fd)):
                                reason = "directory changed"
                            elif (
                                _fingerprint(
                                    os.stat(
                                        candidate.parts[-1],
                                        dir_fd=current_fd,
                                        follow_symlinks=False,
                                    )
                                )
                                != candidate.fingerprint
                            ):
                                reason = "file changed"
                            else:
                                if not dry_run:
                                    os.unlink(candidate.parts[-1], dir_fd=current_fd)
                                    report["deleted"].append(path)
                                report["candidates"].append(path)
                                report["bytes"] += info.st_size
        except OSError as exc:
            reason = f"file unavailable ({exc.__class__.__name__})"
        if reason:
            report["skipped"].append({"path": path, "reason": reason})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=float, default=7, help="intermediate retention (default: 7 days)"
    )
    parser.add_argument(
        "--blurred-days", type=float, default=30, help="blurred video retention (default: 30 days)"
    )
    parser.add_argument("--archive-days", type=float, help="opt in to archived original deletion")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help="project root")
    parser.add_argument("--db", type=Path, help="database path (default: ROOT/reel.db)")
    args = parser.parse_args(argv)
    db = None
    try:
        root = _absolute(args.root)
        db = _ReadOnlyDB(args.db if args.db is not None else root / "reel.db")
        report = cleanup(
            db,
            root,
            days=args.days,
            blurred_days=args.blurred_days,
            archive_days=args.archive_days,
            dry_run=args.dry_run,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Cleanup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.conn.close()
    verb = "Would delete" if args.dry_run else "Deleted"
    for path in report["candidates"]:
        print(f"{verb}: {path}")
    print(
        f"{verb} {len(report['candidates'])} file(s), {report['bytes']} bytes (estimated {report['bytes'] / 1_000_000_000:.6f} GB); skipped {len(report['skipped'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
