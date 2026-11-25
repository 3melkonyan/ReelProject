"""Job runner: ordered stages, resume after crash, retries, stop + notify.

A stage is `fn(ctx) -> dict | None`. Its returned dict is stored and available to
later stages via ctx.out(name). Raise:
  - StopJob(reason)       uncertain result -> stop job, notify, never retry automatically
  - PublishError(retryable=False) -> stop job
  - any other exception   -> retried up to MAX_ATTEMPTS with backoff, then stop
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from contextvars import ContextVar

_JOB_NOTICE = ContextVar("reel_job_notice", default=None)

from reel.db import DB

MAX_ATTEMPTS = 3
BACKOFF_SEC = (0, 30, 120)
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv"}
STABLE_SEC = 60
FOLDER_STABLE_SEC = 300   # a lesson in parts (folder): no part added/grown for 5 min, then it is complete

log = logging.getLogger("reel")


class StopJob(Exception):
    """Uncertain or invalid result: stop and ask the operator."""


@dataclass
class Ctx:
    db: DB
    job_id: int
    source: Path            # archived original (read-only): a video file, or a folder of parts
    workdir: Path
    group: str
    config: dict
    _outputs: dict = field(default_factory=dict)

    def media(self) -> Path:
        """The lesson as ONE video file: the joined parts ('join' stage) or the source file itself."""
        joined = self.out("join").get("file")
        return Path(joined) if joined else self.source

    def out(self, stage: str) -> dict:
        if stage not in self._outputs:
            self._outputs[stage] = self.db.stage_output(self.job_id, stage) or {}
        output = self._outputs[stage]
        if stage == "metadata" and self.db is not None:
            job = dict(self.db.job(self.job_id))
            if job.get("lesson_date"):
                from reel.lesson_dates import dated_metadata
                return dated_metadata(output, job["lesson_date"])
        return output

    def publish_once(self, target: str, do: Callable[[], tuple[str | None, str | None]],
                     find_existing: Callable[[], tuple[str | None, str | None] | None] | None = None
                     ) -> dict:
        """Idempotent publish. `do` returns (external_id, url).

        A `.pending` marker is written before `do()` and removed after the DB row
        exists. If we crash in between, the next attempt first asks the remote
        side (`find_existing`); if the target cannot be queried (Slack, Classroom)
        we STOP instead of risking a duplicate post: the operator resolves it with
        `reel resolve <job> <target> posted|not-posted`."""
        from reel.job_lifecycle import check_cancel
        check_cancel(self.db, self.job_id)
        row = self.db.publication(self.job_id, target)
        if row:
            return {"external_id": row["external_id"], "url": row["url"], "reused": True}
        marker = self.workdir / f"pub_{target}.pending"
        found = find_existing() if find_existing else None
        if found:
            ext_id, url = found
        else:
            if marker.exists() and find_existing is None:
                raise StopJob(f"'{target}' may already be posted (crash during publish). Check it, then "
                              f"`reel resolve {self.job_id} {target} posted|not-posted` and `reel retry {self.job_id}`")
            marker.write_text("pending")
            ext_id, url = do()
        self.db.add_publication(self.job_id, target, ext_id, url)
        marker.unlink(missing_ok=True)
        check_cancel(self.db, self.job_id)
        return {"external_id": ext_id, "url": url, "reused": bool(found)}


Stage = tuple[str, Callable[[Ctx], dict | None]]


def _notify(text: str) -> None:
    log.warning("NOTIFY: %s", text)
    try:
        import os
        if os.environ.get("REEL_DRY_RUN") == "1":
            from reel.stages._dryrun import slack
        else:
            from reel.publish import slack
        receipt = slack.notify(text)
        context = _JOB_NOTICE.get()
        if context and isinstance(receipt,dict) and receipt.get('channel') and receipt.get('ts'):
            from contextlib import closing
            with closing(DB(context[0]/'reel.db').conn) as conn:
                conn.execute('INSERT OR IGNORE INTO service_messages(job_id,channel,timestamp) VALUES(?,?,?)',(context[1],receipt['channel'],receipt['ts']))
        return
    except Exception as e:  # noqa: BLE001 - notification must never crash the runner
        log.warning("slack notify unavailable: %s", e)
    try:  # local fallback: macOS notification
        import subprocess
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "Reel"', "-e", "end run",
                        text[:200]], check=False, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def notify_for_job(root, job_id, text):
    token=_JOB_NOTICE.set((Path(root),job_id))
    try: _notify(text)
    finally: _JOB_NOTICE.reset(token)


def run_job(db, job_id, stages, config, root, notify=_notify, sleep=time.sleep):
    from reel.job_lifecycle import job_lock, check_cancel, JobCancelled
    with job_lock(root,job_id):
        try:
            check_cancel(db,job_id)
            token=_JOB_NOTICE.set((Path(root),job_id))
            try: return _run_job(db,job_id,stages,config,root,notify,sleep)
            finally: _JOB_NOTICE.reset(token)
        except JobCancelled:
            db.conn.execute("UPDATE stages SET status='cancelled' WHERE job_id=? AND status='running'",(job_id,))
            deletion = db.conn.execute('SELECT state FROM lesson_deletions WHERE job_id=?',(job_id,)).fetchone()
            db.set_job(job_id,status='deleted' if deletion and deletion[0]=='done' else 'deleting',current_stage=None)
            return 'stopped'


def _run_job(db: DB, job_id: int, stages: list[Stage], config: dict,
            root: Path, notify: Callable[[str], None] = _notify,
            sleep: Callable[[float], None] = time.sleep) -> str:
    from reel.i18n import language_context
    with language_context(config.get('ui_language','hy')):
        job = db.job(job_id)
        src = Path(job["source_path"])
        archived = root / "archive" / str(job_id) / src.name
        if not src.exists() and archived.exists():    # crash between archive move and DB update
            db.set_job(job_id, source_path=str(archived))
            src = archived
        ctx = Ctx(db, job_id, src, root / "work" / str(job_id),
                  job["grp"], config)
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        db.set_job(job_id, status="running", error=None)

        from reel.progress import Tracker
        progress = Tracker(db, job_id, [n for n, _ in stages])
        for name, fn in stages:
            from reel.job_lifecycle import check_cancel
            check_cancel(db,job_id)
            st = db.stage(job_id, name)
            if st and st["status"] == "done":
                continue
            db.set_job(job_id, current_stage=name)
            while True:
                attempt = db.stage_start(job_id, name)
                log.info("job %s stage %s attempt %s", job_id, name, attempt)
                try:
                    with progress.scope(name):
                        output = fn(ctx)
                    db.stage_done(job_id, name, output)
                    ctx._outputs[name] = output or {}
                    progress.observe(1, force=True)
                    break
                except StopJob as e:
                    db.stage_failed(job_id, name, f"StopJob: {e}")   # never leave a stopped stage 'running'
                    return _stop(db, job_id, name, f"{e}", notify)
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}"
                    db.stage_failed(job_id, name, err + "\n" + traceback.format_exc()[-2000:])
                    retryable = getattr(e, "retryable", True)
                    if not retryable or attempt >= MAX_ATTEMPTS:
                        return _stop(db, job_id, name, err, notify)
                    sleep(BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)])

        db.set_job(job_id, status="done", current_stage=None)
        progress.observe(1, force=True)
        yt = db.publication(job_id, "youtube")
        notify(f"✅ Job {job_id} ({job['grp']}) done" + (f": {yt['url']}" if yt else ""))
        return "done"


def _stop(db: DB, job_id: int, stage: str, reason: str, notify) -> str:
    error = f"[{stage}] {reason}"
    db.set_job(job_id, status="stopped", error=error)
    from reel.hints import hint
    tip = hint(error)
    notify(f"⛔ Job {job_id} stopped at '{stage}': {reason}\n"
           + (f"💡 {tip}\n" if tip else "") + f"Fix, then: reel retry {job_id}")
    return "stopped"


def retry(db: DB, job_id: int) -> None:
    """Re-queue a stopped job. Completed stages are kept and skipped."""
    db.set_job(job_id, status="queued", error=None)


# --- inbox watcher -------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def natural_key(name: str) -> list:
    """Recording order of parts: 'part2' < 'part10' (numbers as numbers, letters case-insensitive)."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.casefold()) for t in re.split(r"(\d+)", name) if t]


def parts_of(folder: Path) -> list[Path]:
    """Video parts of one lesson folder, in recording (natural name) order."""
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")),
                  key=lambda p: natural_key(p.name))


def lesson_digest(parts: list[Path]) -> str:
    """Duplicate check: one file -> its sha256; several parts -> sha256 of the ordered part hashes."""
    if len(parts) == 1:
        return sha256(parts[0])
    h = hashlib.sha256(b"reel-lesson-parts:")
    for p in parts:
        h.update(bytes.fromhex(sha256(p)))
    return h.hexdigest()


def _lessons(inbox: Path):
    """(path, parts, signature, stable_sec) for each lesson waiting in inbox/<group>/:
    a video file, or a folder holding the parts of ONE lesson."""
    for p in sorted(inbox.glob("*/*")):
        if p.name.startswith("."):
            continue
        try:
            if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                yield p, [p], p.stat().st_size, STABLE_SEC
            elif p.is_dir() and (parts := parts_of(p)):
                yield p, parts, tuple((q.name, q.stat().st_size) for q in parts), FOLDER_STABLE_SEC
        except OSError:          # vanished while scanning (being moved or renamed)
            continue


def _free(dest: Path) -> Path:
    """dest, or dest-2, dest-3 ... when taken: never overwrite or merge into an older duplicate."""
    out, k = dest, 2
    while out.exists():
        out, k = dest.with_name(f"{dest.stem}-{k}{dest.suffix}"), k + 1
    return out


def enqueue(db: DB, root: Path, path: Path, parts: list[Path], grp: str, notify=_notify,
            name: str | None = None, subject: str | None = None, lesson_date: str | None = None) -> tuple[int | None, int | None]:
    """One lesson (a video file, or a folder of its parts) -> a queued job, moved to
    archive/<job_id>/<name>. Returns (job_id, None), or (None, existing_job_id) for a duplicate,
    which is moved to archive/duplicates/ instead."""
    name = name or path.name
    what = f"{name} ({len(parts)} parts)" if path.is_dir() else name
    digest = lesson_digest(parts)
    dup = db.find_job_by_sha(digest)
    if dup:
        notify(f"ℹ️ {what} already processed as job {dup['id']} — moved to archive/duplicates")
        dest = _free(root / "archive" / "duplicates" / name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
        return None, dup["id"]
    job_id = db.create_job(str(path), digest, grp, subject, lesson_date=lesson_date)
    dest = root / "archive" / str(job_id) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), dest)  # same volume -> atomic rename, bytes untouched
    db.set_job(job_id, source_path=str(dest))
    log.info("enqueued job %s (%s) from %s", job_id, grp, what)
    return job_id, None


def scan_inbox(db: DB, root: Path, config: dict, notify=_notify,
               _seen: dict[Path, tuple[object, float]] = {}) -> list[int]:  # noqa: B006
    """Enqueue stable new lessons from inbox/<group>/ -- a video file, or a folder with the parts
    of one lesson (joined by the 'join' stage). Moves each to archive/<job_id>/."""
    created = []
    inbox = root / "inbox"
    groups = config.get("groups", {})
    for f, parts, sig, stable in _lessons(inbox):
        t = time.monotonic()
        prev = _seen.get(f)
        if prev is None or prev[0] != sig:
            _seen[f] = (sig, t)          # new, still growing, or a part was added
            continue
        if t - prev[1] < stable:
            continue
        _seen.pop(f, None)
        grp = f.parent.name
        what = f"{f.name} ({len(parts)} parts)" if f.is_dir() else f.name
        if grp not in groups:
            notify(f"⛔ {what}: group '{grp}' is not in config.yaml — left in inbox")
            _seen[f] = (sig, float("inf"))  # don't spam; rescanned after restart
            continue
        job_id, _dup = enqueue(db, root, f, parts, grp, notify)
        if job_id:
            created.append(job_id)
    return created
