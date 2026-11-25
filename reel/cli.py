"""Reel CLI.

  python -m reel.cli run            # daemon: watch inbox, process queue
  python -m reel.cli status [ID]
  python -m reel.cli retry ID
  python -m reel.cli add FILE --group G   # enqueue without waiting for the watcher
  python -m reel.cli resolve ID TARGET posted|not-posted   # after a crash mid-post
  python -m reel.cli cleanup [--dry-run] [--days 7] [--blurred-days 30]   # disk hygiene
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from reel import pipeline
from reel.db import DB

ROOT = Path(__file__).resolve().parent.parent
MIN_FREE_GB = 20
POLL_SEC = 30


def _config() -> dict:
    from reel.config import load_config
    return load_config(str(ROOT / "config.yaml"))


def _config_mtime() -> float:
    try:
        return (ROOT / "config.yaml").stat().st_mtime
    except OSError:
        return 0.0


def _auto_cleanup(db: DB) -> None:
    """Daily disk hygiene in the worker: intermediates of done jobs after 7 days, the blurred
    video after 30 (it is on YouTube). Originals in archive/ are never deleted automatically."""
    try:
        from reel import cleanup
        r = cleanup.cleanup(db, ROOT)
        if r["deleted"]:
            logging.info("cleanup: deleted %d file(s), %.1f GB", len(r["deleted"]), r["bytes"] / 1e9)
    except Exception as e:  # noqa: BLE001 - cleanup must never stop the worker
        logging.warning("cleanup skipped: %s", e)


def _single_worker_lock():
    """Exclusive lock: a second worker (e.g. launchd + a manual run) would process the same job
    twice. The OS releases the lock when the process exits, even on a crash."""
    import fcntl
    path = ROOT / "out" / "worker.lock"
    path.parent.mkdir(exist_ok=True)
    fh = open(path, "w")  # noqa: SIM115 - The returned handle owns the worker lifetime lock.
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.error("another reel worker is already running (out/worker.lock) — exiting")
        sys.exit(1)
    return fh


def cmd_run(db: DB, args) -> None:
    from reel.stages import STAGES
    lock = _single_worker_lock()  # noqa: F841 - held for the life of the process
    cfg = _config()
    for g in cfg["groups"]:
        (ROOT / "inbox" / g).mkdir(parents=True, exist_ok=True)
    logging.info("watching %s", ROOT / "inbox")
    import json
    import os
    hb = ROOT / "out" / "worker.json"
    hb.parent.mkdir(exist_ok=True)
    hb.write_text(json.dumps({"pid": os.getpid(), "started": time.time(),
                              "dry_run": os.environ.get("REEL_DRY_RUN") == "1"}))
    last_cleanup = 0.0
    cfg_mtime = _config_mtime()
    while True:
        if (m := _config_mtime()) != cfg_mtime:          # edited (admin settings page): reload
            cfg_mtime = m
            try:
                cfg = _config()
                for g in cfg["groups"]:
                    (ROOT / "inbox" / g).mkdir(parents=True, exist_ok=True)
                logging.info("config.yaml reloaded")
            except ValueError as e:
                pipeline._notify(f"⛔ config.yaml is invalid, keeping the previous settings: {e}")
        pipeline.scan_inbox(db, ROOT, cfg)
        job = db.next_runnable()
        if job is None:
            if args.once:
                return
            if time.time() - last_cleanup > 86400:      # idle and not run for a day
                last_cleanup = time.time()
                _auto_cleanup(db)
            time.sleep(POLL_SEC)
            continue
        free_gb = shutil.disk_usage(ROOT).free / 1e9
        if free_gb < MIN_FREE_GB:
            pipeline._notify(f"⛔ only {free_gb:.0f} GB free disk — pausing queue")
            time.sleep(600)
            continue
        pipeline.run_job(db, job["id"], STAGES, cfg, ROOT)


def cmd_status(db: DB, args) -> None:
    if args.id:
        j = db.job(args.id)
        print(f"job {j['id']} [{j['status']}] {j['grp']} {j['source_path']}")
        if j["error"]:
            print("  error:", j["error"])
        for s in db.stages(args.id):
            print(f"  {s['name']:<14} {s['status']:<8} attempts={s['attempts']}")
        return
    rows = db.jobs()
    if not rows:
        print("queue is empty")
    for j in rows:
        print(f"{j['id']:>4}  {j['status']:<8} {j['grp']:<12} {j['current_stage'] or '':<14} "
              f"{Path(j['source_path']).name}")


def cmd_retry(db: DB, args) -> None:
    pipeline.retry(db, args.id)
    print(f"job {args.id} re-queued; completed stages will be skipped")


def cmd_resolve(db: DB, args) -> None:
    """After a crash during a Slack/Classroom post: record what really happened."""
    marker = ROOT / "work" / str(args.id) / f"pub_{args.target}.pending"
    if args.state == "posted" and not db.publication(args.id, args.target):
        db.add_publication(args.id, args.target, "manual", None)
    marker.unlink(missing_ok=True)
    print(f"job {args.id} {args.target}: {args.state}; now run: reel retry {args.id}")


def cmd_add(db: DB, args) -> None:
    cfg = _config()
    if args.group not in cfg["groups"]:
        sys.exit(f"unknown group: {args.group}; configure it before adding a lesson")
    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        sys.exit(f"input does not exist: {src}")
    if not src.is_dir() and (not src.is_file() or src.suffix.lower() not in pipeline.VIDEO_EXT):
        sys.exit("input must be a supported video file or a folder of video parts")
    parts = pipeline.parts_of(src) if src.is_dir() else [src]     # a folder = one lesson in parts
    if not parts:
        sys.exit(f"no video files in {src}")
    digest = pipeline.lesson_digest(parts)
    if (dup := db.find_job_by_sha(digest)):
        sys.exit(f"already processed as job {dup['id']}")
    job_id = db.create_job(str(src), digest, args.group)
    dest = ROOT / "archive" / str(job_id) / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        dest.mkdir()
        for p in parts:
            shutil.copy2(p, dest / p.name)  # copy, never move/modify the user's files
    else:
        shutil.copy2(src, dest)  # copy, never move/modify the user's file
    db.set_job(job_id, source_path=str(dest))
    print(f"job {job_id} queued" + (f" ({len(parts)} parts)" if src.is_dir() else ""))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":      # own argparse in reel/cleanup.py
        from reel import cleanup
        sys.exit(cleanup.main(sys.argv[2:]))
    ap = argparse.ArgumentParser(prog="reel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("--once", action="store_true")
    p = sub.add_parser("status"); p.add_argument("id", type=int, nargs="?")
    p = sub.add_parser("retry"); p.add_argument("id", type=int)
    p = sub.add_parser("add"); p.add_argument("file"); p.add_argument("--group", required=True)
    p = sub.add_parser("resolve"); p.add_argument("id", type=int); p.add_argument("target", choices=["slack", "classroom"])
    p.add_argument("state", choices=["posted", "not-posted"])
    args = ap.parse_args()
    db = DB(ROOT / "reel.db")
    {"run": cmd_run, "status": cmd_status, "retry": cmd_retry, "add": cmd_add,
     "resolve": cmd_resolve}[args.cmd](db, args)


if __name__ == "__main__":
    main()
