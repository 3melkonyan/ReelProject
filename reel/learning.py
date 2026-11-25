"""G-40/3: library and student experience data (filters, transcript cues, search, watch progress).

Own tables are created lazily with CREATE TABLE IF NOT EXISTS, so reel/db.py stays untouched
(G-40/1, G-40/2 and G-40/4 change it in parallel). Every function takes an open reel.db.DB.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from reel import captions

_SCHEMA = (
    # Transcript cues on the FINAL video timeline: synced transcript, search and captions (cleanup-safe).
    "CREATE TABLE IF NOT EXISTS lesson_cues (job_id INTEGER NOT NULL REFERENCES jobs(id), idx INTEGER NOT NULL,"
    " t_start REAL NOT NULL, t_end REAL NOT NULL, text TEXT NOT NULL, norm TEXT NOT NULL, PRIMARY KEY(job_id, idx))",
    "CREATE TABLE IF NOT EXISTS lesson_cue_jobs (job_id INTEGER PRIMARY KEY REFERENCES jobs(id),"
    " cues INTEGER NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL)",
    # Per personal account: resume position and the union of really watched seconds (item 4 and 5).
    "CREATE TABLE IF NOT EXISTS watch_progress (user_id INTEGER NOT NULL REFERENCES users(id),"
    " job_id INTEGER NOT NULL REFERENCES jobs(id), position REAL NOT NULL DEFAULT 0,"
    " duration REAL NOT NULL DEFAULT 0, watched_sec REAL NOT NULL DEFAULT 0, intervals TEXT NOT NULL DEFAULT '[]',"
    " completed INTEGER NOT NULL DEFAULT 0, first_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
    " PRIMARY KEY(user_id, job_id))",
    "CREATE INDEX IF NOT EXISTS watch_progress_job ON watch_progress(job_id)",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn) -> None:
    # execute(), not executescript(): executescript would COMMIT a caller's open transaction.
    for statement in _SCHEMA:
        conn.execute(statement)


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# --- 1. library filter: every permitted group and subject, with lesson counts ------------------
def _subject_names(group_cfg: dict | None) -> list[str]:
    names = []
    for item in (group_cfg or {}).get("subjects") or []:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip() and name not in names:
            names.append(name)
    return names


def _metadata_subjects(db, group: str) -> list[str]:
    """Optional G-40/1 compatibility: subject periods stored in group_metadata.subjects_json."""
    try:
        row = db.conn.execute("SELECT subjects_json FROM group_metadata WHERE name=?", (group,)).fetchone()
        items = json.loads(row[0]) if row and row[0] else []
    except Exception:  # noqa: BLE001 - the table/column exists only after G-40/1 is merged
        return []
    return _subject_names({"subjects": items if isinstance(items, list) else []})


def library_facets(records: list[dict], config_groups: dict, completed, scope, db=None) -> dict:
    """Filter options for the library page.

    records: library_records() output, already scoped to the viewer.
    config_groups: config.yaml groups; completed: archived group names;
    scope: None = every group (admin/viewer), else the viewer's own groups.
    Active groups are listed even with 0 lessons; archived groups are listed for admins only when
    they still have lessons, and always for their own students/lecturers (read-only access stays).
    """
    completed = frozenset(completed or ())
    lessons = Counter(r.get("group") for r in records)
    per_subject = Counter((r.get("group"), r.get("subject") or "") for r in records)
    names = [name for name in config_groups if scope is None or name in scope]
    names += sorted({r.get("group") for r in records if r.get("group")} - set(names))
    groups = []
    for name in names:
        archived = name in completed
        if archived and scope is None and not lessons[name]:
            continue
        subjects = _subject_names(config_groups.get(name))
        if db is not None:
            subjects += [s for s in _metadata_subjects(db, name) if s not in subjects]
        subjects += sorted({s for (g, s) in per_subject if g == name and s and s not in subjects})
        groups.append({"name": name, "lessons": lessons[name], "archived": archived,
                       "subjects": [{"name": s, "lessons": per_subject[(name, s)]} for s in subjects]})
    groups.sort(key=lambda g: (g["archived"], g["name"].casefold()))
    return {"groups": groups, "total": len(records)}


# --- 2. transcript cues (final timeline), lazily rebuilt for older lessons ------------------------
def transcript_cues(db, job_id: int, work: Path) -> tuple[list[dict], str]:
    """Cues for one lesson from its work folder: (cues, source). Missing files -> ([], 'missing')."""
    plan = db.stage_output(job_id, "segments") or {}
    folder = Path(work) / str(job_id)
    final = folder / "transcript_final.json"           # G-40/2 smart pauses: already on the final timeline
    try:
        if plan.get("time_map"):
            if not final.is_file():
                return [], "missing_final_timeline"
            data = json.loads(final.read_text(encoding="utf-8"))
            segments = data.get("segments", []) if isinstance(data, dict) else data
            return captions.final_cues(segments), "final"
        if not plan.get("keep"):
            return [], "missing"
        data = json.loads((folder / "transcript" / "transcript.json").read_text(encoding="utf-8"))
        segments = data.get("segments", []) if isinstance(data, dict) else data
        return captions.final_cues(segments, plan["keep"]), "transcript"
    except (OSError, ValueError, AttributeError, TypeError):
        return [], "missing"


def save_cues(db, job_id: int, cues: list[dict], source: str) -> None:
    ensure_schema(db.conn)
    db.conn.execute("SAVEPOINT reel_cues")
    try:
        db.conn.execute("DELETE FROM lesson_cues WHERE job_id=?", (job_id,))
        db.conn.executemany("INSERT INTO lesson_cues(job_id,idx,t_start,t_end,text,norm) VALUES(?,?,?,?,?,?)",
                            [(job_id, i, c["start"], c["end"], c["text"], captions.norm(c["text"]))
                             for i, c in enumerate(cues)])
        db.conn.execute("INSERT INTO lesson_cue_jobs(job_id,cues,source,created_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(job_id) DO UPDATE SET cues=excluded.cues, source=excluded.source, "
                        "created_at=excluded.created_at", (job_id, len(cues), source, _now()))
        db.conn.execute("RELEASE reel_cues")
    except BaseException:
        db.conn.execute("ROLLBACK TO reel_cues")
        db.conn.execute("RELEASE reel_cues")
        raise


def ensure_cues(db, job_ids, work: Path) -> None:
    """Index lessons published before G-40/3 once (the transcript.json survives cleanup)."""
    ensure_schema(db.conn)
    ids = [int(i) for i in job_ids]
    done = {row[0] for row in db.conn.execute(
        f"SELECT job_id FROM lesson_cue_jobs WHERE job_id IN ({','.join('?' * len(ids)) or 'NULL'}) "
        "AND source NOT IN ('missing','missing_final_timeline')", ids)}
    for job_id in ids:
        if job_id not in done:
            save_cues(db, job_id, *transcript_cues(db, job_id, work))


def lesson_cues(db, job_id: int) -> list[dict]:
    ensure_schema(db.conn)
    return [{"start": r[0], "end": r[1], "text": r[2]} for r in db.conn.execute(
        "SELECT t_start,t_end,text FROM lesson_cues WHERE job_id=? ORDER BY idx", (job_id,))]


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(db, query: str, job_ids, *, per_lesson: int = 5, lessons: int = 30) -> dict:
    """Every query word must occur (any order, partial words: Armenian endings vary).

    Searches the spoken transcript and, when G-40/2's ocr_segments table exists, board/slide text.
    Times are on the final video timeline. Returns {'terms', 'results': [{job_id, hits, total}]}.
    """
    terms = norm_terms(query)
    ids = [int(i) for i in job_ids]
    if not terms or not ids:
        return {"terms": terms, "results": []}
    ensure_schema(db.conn)
    marks = ",".join("?" * len(ids))
    hits: dict[int, list[dict]] = {}
    rows = db.conn.execute(
        f"SELECT job_id,t_start,t_end,text FROM lesson_cues WHERE job_id IN ({marks}) AND "
        + " AND ".join("norm LIKE ? ESCAPE '\\'" for _ in terms) + " ORDER BY job_id, t_start LIMIT 5000",
        (*ids, *map(_like, terms)))
    for job_id, start, end, text in rows:
        hits.setdefault(job_id, []).append({"t": start, "end": end, "text": text, "source": "speech"})
    if _table_exists(db.conn, "ocr_segments"):
        for job_id, start, end, source, text in db.conn.execute(
                f"SELECT job_id,t_start,t_end,source,text FROM ocr_segments WHERE job_id IN ({marks}) "
                "ORDER BY job_id, t_start LIMIT 20000", ids):
            flat = captions.norm(text)
            if all(term in flat for term in terms):
                hits.setdefault(job_id, []).append({"t": start, "end": end, "text": " ".join(str(text).split()),
                                                    "source": "board" if source == "board" else "slide"})
    results = []
    for job_id, found in hits.items():
        found.sort(key=lambda h: h["t"])
        results.append({"job_id": job_id, "total": len(found), "hits": found[:per_lesson]})
    results.sort(key=lambda r: (-r["total"], -r["job_id"]))
    return {"terms": terms, "results": results[:lessons]}


def norm_terms(query: object) -> list[str]:
    """At most 6 distinct search words; a query of one letter matches too much and is ignored."""
    terms = list(dict.fromkeys(captions.norm(query).split()))[:6]
    return terms if sum(map(len, terms)) >= 2 else []


# --- 3. AI study notes ------------------------------------------------------------------------------
def public_summary(output: dict | None) -> dict | None:
    """The summary stage output as shown to students; None when skipped, missing or malformed."""
    if not isinstance(output, dict) or output.get("skipped"):
        return None
    summary, terms, questions = output.get("summary"), output.get("terms"), output.get("questions")
    if not (isinstance(summary, list) and isinstance(terms, list) and isinstance(questions, list)) or not summary:
        return None
    return {"summary": [str(x) for x in summary], "questions": [str(x) for x in questions],
            "terms": [{"term": str(t.get("term", "")), "meaning": str(t.get("meaning", ""))}
                      for t in terms if isinstance(t, dict)],
            "generated_at": output.get("generated_at")}


# --- 4. watch progress (resume + accurate watched share) ------------------------------------------------
MAX_LESSON_SEC = 6 * 3600
COMPLETE_SHARE = 0.9


def _merge(intervals: list, gap: float = 0.0) -> list[list[float]]:
    # Merge overlap only. Filling seek gaps would invent watched time.
    out: list[list[float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _seconds(value, limit: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= limit else None


def record_progress(db, user_id: int, job_id: int, position, duration, intervals, *,
                    known_duration: float | None = None) -> dict:
    """Merge newly watched [start, end] seconds into the stored union. Seeks never count as watched.

    known_duration (the edited video length from the pipeline) wins over the browser-reported one."""
    ensure_schema(db.conn)
    length = _seconds(known_duration, MAX_LESSON_SEC) or _seconds(duration, MAX_LESSON_SEC) or 0.0
    limit = length + 5 if length else MAX_LESSON_SEC
    if not isinstance(intervals, list) or len(intervals) > 500:
        raise ValueError("intervals must be a list of at most 500 [start, end] pairs")
    clean = []
    for item in intervals:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("every interval is [start, end]")
        a, b = _seconds(item[0], limit), _seconds(item[1], limit)
        if a is None or b is None or b < a:
            raise ValueError("interval outside the lesson")
        if length:
            a, b = min(a, length), min(b, length)
        if b - a >= 0.2:
            clean.append([round(a, 1), round(b, 1)])
    spot = _seconds(position, limit)
    if spot is None:
        raise ValueError("position outside the lesson")
    if length:
        spot = min(spot, length)
    now = _now()
    db.conn.execute("SAVEPOINT reel_progress")
    try:
        row = db.conn.execute("SELECT intervals, first_at FROM watch_progress WHERE user_id=? AND job_id=?",
                              (user_id, job_id)).fetchone()
        merged = _merge((json.loads(row[0]) if row else []) + clean)
        watched = round(sum(b - a for a, b in merged), 1)
        share = min(1.0, watched / length) if length else 0.0
        done = int(share >= COMPLETE_SHARE)
        db.conn.execute(
            "INSERT INTO watch_progress(user_id,job_id,position,duration,watched_sec,intervals,completed,first_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,job_id) DO UPDATE SET "
            "position=excluded.position, duration=excluded.duration, watched_sec=excluded.watched_sec, "
            "intervals=excluded.intervals, completed=max(completed, excluded.completed), "
            "updated_at=excluded.updated_at",
            (user_id, job_id, round(spot, 1), length, watched, json.dumps(merged), done, row[1] if row else now, now))
        db.conn.execute("RELEASE reel_progress")
    except BaseException:
        db.conn.execute("ROLLBACK TO reel_progress")
        db.conn.execute("RELEASE reel_progress")
        raise
    return {"position": round(spot, 1), "watched_sec": watched, "percent": round(share * 100, 1),
            "completed": bool(done)}


def progress_for(db, user_id: int, job_ids=None) -> dict[int, dict]:
    ensure_schema(db.conn)
    out = {}
    for job_id, position, duration, watched, completed, updated in db.conn.execute(
            "SELECT job_id, position, duration, watched_sec, completed, updated_at FROM watch_progress "
            "WHERE user_id=?", (user_id,)):
        if job_ids is None or job_id in job_ids:
            out[job_id] = {"job_id": job_id, "position": position, "duration": duration, "watched_sec": watched,
                           "percent": round(min(1.0, watched / duration) * 100, 1) if duration else 0.0,
                           "completed": bool(completed), "updated_at": updated}
    return out
