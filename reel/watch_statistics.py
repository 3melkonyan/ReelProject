"""Read-only, group-scoped watch analytics for personal administrators, lecturers and TAs."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

from reel.insights import video_id

COMPLETE_PERCENT = 90


def _columns(conn, table):
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is None
    ):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _memberships(value):
    try:
        groups = json.loads(value)
    except (TypeError, ValueError):
        return frozenset()
    return (
        frozenset(group for group in groups if isinstance(group, str))
        if isinstance(groups, list)
        else frozenset()
    )


def allowed_groups(database, principal, known_groups=()):
    """None denies access; an empty set is a permitted account with no group data."""
    if (
        not isinstance(principal, dict)
        or principal.get("legacy")
        or type(principal.get("id")) is not int
    ):
        return None
    row = database.conn.execute("SELECT * FROM users WHERE id=?", (principal["id"],)).fetchone()
    if row is None:
        return None
    user = dict(row)
    if not user.get("active") or user.get("removed_at") or user.get("deleted_at"):
        return None
    memberships = _memberships(user.get("groups_json"))
    if user["role"] == "admin":
        return frozenset(known_groups)
    if user["role"] == "lecturer":
        return memberships
    if user["role"] != "student" or not {"name", "ta_ids_json"} <= _columns(
        database.conn, "group_metadata"
    ):
        return None
    assigned = set()
    for name, raw in database.conn.execute("SELECT name,ta_ids_json FROM group_metadata"):
        if name not in memberships:
            continue
        try:
            ids = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(ids, list) and any(
            type(ident) is int and ident == user["id"] for ident in ids
        ):
            assigned.add(name)
    return frozenset(assigned) if assigned else None


def available(database, principal):
    """A cheap permission hint for the library UI; it exposes no student data."""
    return allowed_groups(database, principal) is not None


def _number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _published(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC).timestamp() if stamp.tzinfo is None else stamp.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _progress(database, ids):
    required = {"user_id", "job_id", "watched_sec", "duration", "updated_at"}
    if not ids or not required <= _columns(database.conn, "watch_progress"):
        return {}
    # Bound SQL variable counts for a library larger than SQLite's parameter limit.
    result = {}
    for offset in range(0, len(ids), 400):
        batch = ids[offset : offset + 400]
        marks = ",".join("?" for _ in batch)
        for row in database.conn.execute(
            f"SELECT user_id,job_id,watched_sec,duration,updated_at FROM watch_progress WHERE job_id IN ({marks})",
            batch,
        ):
            result[(row["user_id"], row["job_id"])] = dict(row)
    return result


def statistics(database, records, principal, *, group=None, known_groups=()):
    """records must come from insights.library_records: genuine publication plus passed privacy.

    Progress alone never adds a lesson or enrolls a user. A missed pair means no watched seconds
    in either of the group's two latest publications, rather than two incomplete lessons.
    """
    lessons = {
        record["job_id"]: dict(record)
        for record in records
        if type(record.get("job_id")) is int
        and record["job_id"] > 0
        and not record.get("demo")
        and record.get("has_video") is not False
        and video_id(record.get("url"))
    }
    names = set(known_groups) | {
        lesson["group"] for lesson in lessons.values() if isinstance(lesson.get("group"), str)
    }
    scope = allowed_groups(database, principal, names)
    if scope is None:
        raise PermissionError("watch statistics are restricted to personal staff accounts")
    selected = group.strip() if isinstance(group, str) else None
    if selected and len(selected) > 120:
        raise ValueError("group filter is too long")
    if selected and selected not in scope:
        raise LookupError("group is outside the statistics scope")
    visible = {selected} if selected else set(scope)
    ordered = sorted(
        (lesson for lesson in lessons.values() if lesson.get("group") in visible),
        key=lambda lesson: (_published(lesson.get("published_at")), lesson["job_id"]),
        reverse=True,
    )
    progress = _progress(database, [lesson["job_id"] for lesson in ordered])
    roster = {}
    for row in database.conn.execute(
        "SELECT * FROM users WHERE role='student' AND active=1 ORDER BY id"
    ):
        user = dict(row)
        if user.get("removed_at") or user.get("deleted_at"):
            continue
        for name in _memberships(user.get("groups_json")) & visible:
            roster.setdefault(name, []).append({"id": user["id"], "name": user["name"]})
    by_group = {name: [lesson for lesson in ordered if lesson["group"] == name] for name in visible}
    students, missed = [], []
    metrics = {lesson["job_id"]: [] for lesson in ordered}
    for name in sorted(visible, key=str.casefold):
        group_lessons = by_group[name]
        for student in roster.get(name, []):
            viewed = []
            for lesson in group_lessons:
                row = progress.get((student["id"], lesson["job_id"]), {})
                duration = _number(lesson.get("duration")) or _number(row.get("duration"))
                watched = _number(row.get("watched_sec"))
                if duration:
                    watched = min(watched, duration)
                percent = min(100.0, watched / duration * 100) if duration else 0.0
                # Compare before rounding: 89.96% must not become a completed lesson.
                item = {
                    "job_id": lesson["job_id"],
                    "watched_sec": round(watched, 1),
                    "percent": round(percent, 1),
                    "completed": percent >= COMPLETE_PERCENT,
                    "updated_at": row.get("updated_at"),
                }
                viewed.append(item)
                metrics[lesson["job_id"]].append(item)
            times = [item["updated_at"] for item in viewed if item["updated_at"]]
            students.append(
                {
                    **student,
                    "group": name,
                    "watched_sec": round(sum(item["watched_sec"] for item in viewed), 1),
                    "percent": round(sum(item["percent"] for item in viewed) / len(viewed), 1)
                    if viewed
                    else 0.0,
                    "completed": sum(item["completed"] for item in viewed),
                    "total_lessons": len(viewed),
                    "last_watched_at": max(times, key=_published) if times else None,
                    "lessons": viewed,
                }
            )
            latest = viewed[:2]
            if len(latest) == 2 and all(item["watched_sec"] == 0 for item in latest):
                missed.append(
                    {
                        **student,
                        "group": name,
                        "lessons": [
                            {key: lesson.get(key) for key in ("job_id", "title", "published_at")}
                            for lesson in group_lessons[:2]
                        ],
                    }
                )
    lesson_stats = []
    for lesson in ordered:
        items = metrics[lesson["job_id"]]
        lesson_stats.append(
            {
                key: lesson.get(key)
                for key in ("job_id", "title", "group", "subject", "published_at")
            }
            | {
                "duration": _number(lesson.get("duration")),
                "students": len(items),
                "started": sum(item["watched_sec"] > 0 for item in items),
                "completed": sum(item["completed"] for item in items),
                "average_percent": round(sum(item["percent"] for item in items) / len(items), 1)
                if items
                else 0.0,
            }
        )
    return {
        "groups": [{"name": name} for name in sorted(scope, key=str.casefold)],
        "lessons": lesson_stats,
        "students": students,
        "missed": missed,
        "selected_group": selected or None,
        "threshold": COMPLETE_PERCENT,
    }
