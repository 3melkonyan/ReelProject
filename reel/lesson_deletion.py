"""Durable, resumable lesson retirement. Source recordings and audit history are retained."""

import json
import os
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from reel.audit import audit_connection
from reel.db import DB
from reel.job_lifecycle import job_lock


def stamp():
    return datetime.now(UTC).isoformat()


def state(database, job_id):
    row = database.conn.execute(
        "SELECT * FROM lesson_deletions WHERE job_id=?", (job_id,)
    ).fetchone()
    return (
        {"state": row["state"], "steps": json.loads(row["steps"]), "error": row["error"]}
        if row
        else None
    )


def _save(conn, job_id, status, steps, error=None):
    conn.execute(
        "UPDATE lesson_deletions SET state=?,steps=?,error=?,updated_at=? WHERE job_id=?",
        (status, json.dumps(steps), error, stamp(), job_id),
    )


def _backup(database, root, job_id):
    folder = root / "archive" / "deleted" / str(job_id)
    if any(p.is_symlink() for p in (root / "archive", root / "archive" / "deleted", folder)):
        raise ValueError("Invalid backup directory")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "database.json"
    if path.is_symlink():
        raise ValueError("Invalid backup file")
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get("job", {}).get("id") != job_id:
            raise ValueError("Invalid backup")
        return
    original = database.conn.execute(
        "SELECT original_job FROM lesson_deletions WHERE job_id=?", (job_id,)
    ).fetchone()
    data = {
        "job": json.loads(original[0]) if original and original[0] else dict(database.job(job_id))
    }
    import re

    for row in database.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall():
        table = row[0]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            continue
        columns = {r[1] for r in database.conn.execute(f'PRAGMA table_info("{table}")')}
        if "job_id" in columns:
            data[table] = [
                dict(r)
                for r in database.conn.execute(f'SELECT * FROM "{table}" WHERE job_id=?', (job_id,))
            ]
    data["audit_events"] = [
        dict(r)
        for r in database.conn.execute(
            "SELECT * FROM audit_events WHERE target=?", (f"job:{job_id}",)
        )
    ]
    fd, temporary = tempfile.mkstemp(prefix=".backup-", dir=folder)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _publishers():
    if os.environ.get("REEL_DRY_RUN") == "1":
        from reel.stages._dryrun import slack, youtube
    else:
        from reel.publish import slack, youtube
    return youtube, slack


def _slack(database, job_id, adapter):
    publication = database.publication(job_id, "slack")
    if publication and publication["external_id"]:
        row = database.conn.execute(
            "SELECT channel FROM publication_channels WHERE job_id=? AND target='slack'", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError("missing_slack_channel")
        adapter.delete_message(row[0], publication["external_id"])
    for row in database.conn.execute(
        "SELECT * FROM service_messages WHERE job_id=? AND deleted=0", (job_id,)
    ).fetchall():
        adapter.delete_message(row["channel"], row["timestamp"])
        database.conn.execute(
            "UPDATE service_messages SET deleted=1 WHERE job_id=? AND channel=? AND timestamp=?",
            (job_id, row["channel"], row["timestamp"]),
        )


def resume(root, job_id):
    root = Path(root)
    with closing(DB(root / "reel.db").conn) as conn:
        # Use DB's publication helpers while keeping one connection for the operation.
        database = DB.__new__(DB)
        database.conn = conn
        current = state(database, job_id)
        if not current or current["state"] == "done":
            return current
        with job_lock(root, job_id, "deletion", False) as owns:
            if not owns:
                return state(database, job_id)
            current = state(database, job_id)
            if current["state"] == "done":
                return current
            with job_lock(root, job_id, wait=False) as stopped:
                if not stopped:
                    _save(conn, job_id, "waiting", current["steps"])
                    return state(database, job_id)
                steps = current["steps"]
                _save(conn, job_id, "running", steps)
                youtube, slack = _publishers()

                def backup():
                    _backup(database, root, job_id)

                def pending():
                    for target in ("youtube", "slack"):
                        marker = root / "work" / str(job_id) / f"pub_{target}.pending"
                        if marker.exists() and not database.publication(job_id, target):
                            raise ValueError("unresolved_publication")

                def delete_youtube():
                    row = database.publication(job_id, "youtube")
                    if row and row["external_id"]:
                        youtube.delete_video(row["external_id"])

                def library():
                    from reel.publish.library import remove_job

                    remove_job(root / "library.jsonl", job_id)

                for step, action in [
                    ("backup", backup),
                    ("reconcile", pending),
                    ("youtube", delete_youtube),
                    ("slack", lambda: _slack(database, job_id, slack)),
                    ("library", library),
                ]:
                    if steps.get(step) == "done":
                        continue
                    try:
                        action()
                        steps[step] = "done"
                        _save(conn, job_id, "running", steps)
                    except Exception as error:
                        steps[step] = "failed"
                        code = (
                            str(error)
                            if str(error) in ("missing_slack_channel", "unresolved_publication")
                            else type(error).__name__
                        )
                        _save(conn, job_id, "partial", steps, code)
                        return state(database, job_id)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = database.job(job_id)
                    if not row["sha256"].startswith(f"superseded-{job_id}-"):
                        conn.execute(
                            "UPDATE jobs SET sha256=?,status=?,current_stage=NULL,updated_at=? WHERE id=?",
                            (f"superseded-{job_id}-" + row["sha256"], "deleted", stamp(), job_id),
                        )
                        conn.execute(
                            "UPDATE publications SET target='old-'||target WHERE job_id=? AND target NOT LIKE 'old-%'",
                            (job_id,),
                        )
                        audit_connection(
                            conn,
                            conn.execute(
                                "SELECT actor FROM lesson_deletions WHERE job_id=?", (job_id,)
                            ).fetchone()[0],
                            "job.deleted",
                            f"job:{job_id}",
                            {"status": "deleted"},
                        )
                    _save(conn, job_id, "done", steps)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.rollback()
                    raise
        return state(database, job_id)


def request(database, root, job_id, actor, slack_channel=None):
    original = dict(database.job(job_id))
    database.conn.execute("BEGIN IMMEDIATE")
    try:
        database.conn.execute(
            "INSERT OR IGNORE INTO lesson_deletions(job_id,state,actor,original_job,created_at,updated_at) VALUES(?,'requested',?,?,?,?)",
            (job_id, actor, json.dumps(original), stamp(), stamp()),
        )
        database.conn.execute(
            "UPDATE lecturer_reviews SET status='cancelled' WHERE job_id=?", (job_id,)
        )
        if state(database, job_id)["state"] != "done":
            database.conn.execute(
                "UPDATE jobs SET status='deleting',updated_at=? WHERE id=?", (stamp(), job_id)
            )
        if slack_channel:
            database.conn.execute(
                "INSERT OR REPLACE INTO publication_channels VALUES(?,'slack',?)",
                (job_id, slack_channel),
            )
        audit_connection(database.conn, actor, "job.delete_request", f"job:{job_id}", {})
        database.conn.execute("COMMIT")
    except BaseException:
        database.conn.rollback()
        raise
    return resume(root, job_id)
