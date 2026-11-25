"""SQLite persistence: jobs, per-stage state, publications (idempotency)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS group_course_links(name TEXT PRIMARY KEY, course_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 login TEXT NOT NULL UNIQUE COLLATE NOCASE,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','lecturer','student')),
 groups_json TEXT NOT NULL DEFAULT '[]',
 avatar TEXT,
 active INTEGER NOT NULL DEFAULT 1,
 session_version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,
 last_login_at TEXT,
 removed_at TEXT,
 deleted_at TEXT,
 demo_tag TEXT
);
CREATE TABLE IF NOT EXISTS user_languages (
 user_id INTEGER PRIMARY KEY REFERENCES users(id),
 language TEXT NOT NULL CHECK(language IN ('hy','en'))
);
CREATE TABLE IF NOT EXISTS lesson_deletions (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id), state TEXT NOT NULL,
 actor INTEGER, original_job TEXT, steps TEXT NOT NULL DEFAULT '{}', error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_channels (
 job_id INTEGER NOT NULL REFERENCES jobs(id), target TEXT NOT NULL, channel TEXT NOT NULL,
 PRIMARY KEY(job_id,target)
);
CREATE TABLE IF NOT EXISTS service_messages (
 job_id INTEGER NOT NULL REFERENCES jobs(id), channel TEXT NOT NULL, timestamp TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job_id,channel,timestamp)
);
CREATE TABLE IF NOT EXISTS lecturer_reviews (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id), uploader_id INTEGER, owner_token TEXT,
 status TEXT NOT NULL DEFAULT 'pending', cursor INTEGER NOT NULL DEFAULT 1,
 decision TEXT, answered_by INTEGER, answered_role TEXT, answered_at TEXT,
 automatic INTEGER NOT NULL DEFAULT 0, started_at REAL, error TEXT,
 notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lecturer_reference_sets (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id), created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS in_app_notifications (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), user_id INTEGER,
 administrators INTEGER NOT NULL DEFAULT 0, message_key TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(job_id,user_id,administrators,message_key)
);
CREATE TABLE IF NOT EXISTS account_tokens (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 digest TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('invite','reset')),
 user_id INTEGER REFERENCES users(id),
 payload TEXT NOT NULL DEFAULT '{}',
 created_by INTEGER REFERENCES users(id),
 created_at REAL NOT NULL,
 expires_at REAL NOT NULL,
 used_at REAL,
 revoked_at REAL,
 registered_user_id INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS account_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS group_metadata (
 name TEXT PRIMARY KEY,
 course_start TEXT,
 course_end TEXT,
 subjects_json TEXT NOT NULL DEFAULT '[]',
 ta_ids_json TEXT NOT NULL DEFAULT '[]',
 status_override TEXT,
 updated_at TEXT NOT NULL,
 demo_tag TEXT
);
CREATE TABLE IF NOT EXISTS group_notifications (
 name TEXT NOT NULL,
 end_date TEXT NOT NULL,
 claimed_at TEXT NOT NULL,
 sent_at TEXT,
 PRIMARY KEY(name,end_date)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 created_at TEXT NOT NULL,
 actor INTEGER,
 action TEXT NOT NULL,
 target TEXT NOT NULL,
 details TEXT NOT NULL DEFAULT '{}',
 demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS demo_registry (
 kind TEXT NOT NULL,
 key TEXT NOT NULL,
 owner_tag TEXT NOT NULL,
 payload_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(kind,key)
);
CREATE TABLE IF NOT EXISTS demo_lessons (
 id TEXT PRIMARY KEY,
 group_name TEXT NOT NULL,
 subject TEXT NOT NULL,
 title TEXT NOT NULL,
 description TEXT NOT NULL,
 lesson_date TEXT NOT NULL,
 thumbnail TEXT NOT NULL DEFAULT '',
 lecturer_id INTEGER REFERENCES users(id),
 duration_sec REAL NOT NULL DEFAULT 0,
 demo_tag TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_limits (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, locked_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  grp TEXT NOT NULL,
  lesson_date TEXT,                          -- recording/user date; NULL in legacy jobs
  subject TEXT,                               -- the group's subject (playlist) chosen at upload
  status TEXT NOT NULL DEFAULT 'queued',      -- queued | running | stopped | done
  current_stage TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stages (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL,                       -- running | done | failed
  attempts INTEGER NOT NULL DEFAULT 0,
  output TEXT,
  error TEXT,
  started_at TEXT,
  finished_at TEXT,
  PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS job_insights (
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS publications (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  target TEXT NOT NULL,                       -- youtube | playlist | slack | classroom | library
  external_id TEXT,
  url TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, target)
);
"""


def email_identity(value) -> str:
    """Compare mailbox identities across Unicode/IDNA domains without rewriting rows."""
    if not isinstance(value, str):
        return ""
    value = value.strip().casefold()
    local, separator, domain = value.rpartition("@")
    if separator:
        try:
            return local + "@" + domain.encode("idna").decode("ascii").casefold()
        except UnicodeError:
            pass  # Malformed legacy values must not break unrelated directory queries.
    return value


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path = "reel.db"):
        self.conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("casefold", 1, lambda value: value.casefold() if isinstance(value, str) else "", deterministic=True)
        self.conn.create_function("email_identity", 1, email_identity, deterministic=True)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(users)")}
        for column in ("removed_at", "deleted_at", "demo_tag"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
        if "subject" not in {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN subject TEXT")   # reel.db from before subjects

        if "lesson_date" not in {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}:
            try:
                self.conn.execute("ALTER TABLE jobs ADD COLUMN lesson_date TEXT")
            except sqlite3.OperationalError:
                if "lesson_date" not in {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}:
                    raise

    # --- jobs -------------------------------------------------------------
    def find_job_by_sha(self, sha: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE sha256=?", (sha,)).fetchone()

    def create_job(self, source_path: str, sha: str, grp: str, subject: str | None = None, lesson_date: str | None = None) -> int | None:
        """Returns new job id, or None if this file (sha256) was already seen."""
        if lesson_date is not None:
            from reel.lesson_dates import validate
            lesson_date = validate(lesson_date)
        try:
            cur = self.conn.execute(
                "INSERT INTO jobs (source_path, sha256, grp, subject, lesson_date, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)", (source_path, sha, grp, subject, lesson_date, now(), now()))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def job(self, job_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return row

    def jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def next_runnable(self) -> sqlite3.Row | None:
        # 'running' = interrupted by crash/restart; resume it first.
        return self.conn.execute(
            "SELECT * FROM jobs WHERE status IN ('running','queued') AND NOT EXISTS (SELECT 1 FROM lesson_deletions d WHERE d.job_id=jobs.id)"
            " ORDER BY status='running' DESC, id LIMIT 1").fetchone()

    def set_job(self, job_id: int, **fields) -> None:
        fields["updated_at"] = now()
        retry_transition = fields.get("status") == "queued"
        owns_transaction = retry_transition and not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self.conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone() if retry_transition else None
            cols = ", ".join(f"{key}=?" for key in fields)
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            if previous and previous["status"] == "stopped":
                from reel.audit import write_event
                write_event(self, actor=None, action="job.retry", target=f"job:{job_id}", details={})
            if owns_transaction:
                self.conn.execute("COMMIT")
        except BaseException:
            if owns_transaction and self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    # --- stages -----------------------------------------------------------
    def stage(self, job_id: int, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM stages WHERE job_id=? AND name=?", (job_id, name)).fetchone()

    def stage_output(self, job_id: int, name: str) -> dict | None:
        row = self.stage(job_id, name)
        return json.loads(row["output"]) if row and row["output"] else None

    def stage_start(self, job_id: int, name: str) -> int:
        self.conn.execute(
            "INSERT INTO stages (job_id, name, status, attempts, started_at)"
            " VALUES (?,?, 'running', 1, ?)"
            " ON CONFLICT(job_id, name) DO UPDATE SET status='running',"
            " attempts=attempts+1, error=NULL, started_at=excluded.started_at",
            (job_id, name, now()))
        return self.stage(job_id, name)["attempts"]

    def stage_done(self, job_id: int, name: str, output: dict | None) -> None:
        self.conn.execute(
            "UPDATE stages SET status='done', output=?, finished_at=? WHERE job_id=? AND name=?",
            (json.dumps(output or {}, ensure_ascii=False), now(), job_id, name))

    def stage_failed(self, job_id: int, name: str, error: str) -> None:
        self.conn.execute(
            "UPDATE stages SET status='failed', error=?, finished_at=? WHERE job_id=? AND name=?",
            (error, now(), job_id, name))

    def stages(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM stages WHERE job_id=? ORDER BY started_at", (job_id,)).fetchall()

    # --- publications (idempotency) ---------------------------------------
    def publication(self, job_id: int, target: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM publications WHERE job_id=? AND target=?", (job_id, target)).fetchone()

    def add_publication(self, job_id: int, target: str, external_id: str | None,
                        url: str | None) -> None:
        """Raises sqlite3.IntegrityError if (job_id, target) already exists."""
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO publications (job_id, target, external_id, url, created_at)"
                " VALUES (?,?,?,?,?)", (job_id, target, external_id, url, now()))
            from reel.audit import write_event
            write_event(self, actor=None, action="publication", target=f"job:{job_id}", details={"target":target})
            if owns_transaction:
                self.conn.execute("COMMIT")
        except BaseException:
            if owns_transaction and self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def insights(self, job_id: int) -> dict:
        row = self.conn.execute("SELECT data FROM job_insights WHERE job_id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_insights(self, job_id: int, data: dict) -> None:
        self.conn.execute("INSERT INTO job_insights(job_id,data) VALUES(?,?) "
                          "ON CONFLICT(job_id) DO UPDATE SET data=excluded.data",
                          (job_id, json.dumps(data, ensure_ascii=False, allow_nan=False)))
