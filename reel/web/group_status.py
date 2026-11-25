"""Calendar-based completion with manual overrides and no background scheduler."""

import json
from contextlib import closing
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from reel.i18n import t as _t


def calendar_day(value=None):
    if value is None:
        return datetime.now(ZoneInfo("Asia/Yerevan")).date()
    return value if isinstance(value, date) else date.fromisoformat(value)


def legacy_completed(database):
    row = database.conn.execute(
        "SELECT value FROM account_settings WHERE key='completed_groups'"
    ).fetchone()
    return set(json.loads(row[0])) if row else set()


def store_legacy_completed(database, names):
    database.conn.execute(
        "INSERT INTO account_settings(key,value) VALUES('completed_groups',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(sorted(names), ensure_ascii=False),),
    )


def completed_groups(database, today=None):
    current = calendar_day(today).isoformat()
    names = legacy_completed(database)
    for row in database.conn.execute("SELECT name,course_end,status_override FROM group_metadata"):
        if row["status_override"] == "active":
            names.discard(row["name"])
        elif row["status_override"] == "completed" or (
            row["course_end"] and row["course_end"] < current
        ):
            names.add(row["name"])
    return frozenset(names)


def set_completed(database, name, completed, *, actor=None, today=None):
    """The end date is inclusive; reactivating an expired course is an explicit override."""
    from reel.audit import write_event
    from reel.db import now

    owns_transaction = not database.conn.in_transaction
    if owns_transaction:
        database.conn.execute("BEGIN IMMEDIATE")
    try:
        names = legacy_completed(database)
        (names.add if completed else names.discard)(name)
        store_legacy_completed(database, names)
        row = database.conn.execute(
            "SELECT course_end,demo_tag FROM group_metadata WHERE name=?", (name,)
        ).fetchone()
        past_end = bool(
            row and row["course_end"] and row["course_end"] < calendar_day(today).isoformat()
        )
        override = "completed" if completed else "active" if past_end else None
        database.conn.execute(
            "INSERT INTO group_metadata(name,status_override,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET status_override=excluded.status_override,updated_at=excluded.updated_at",
            (name, override, now()),
        )
        write_event(
            database,
            actor=actor,
            action="group.complete" if completed else "group.restore",
            target="group:" + name,
            details={},
            demo=bool(row and row["demo_tag"]),
        )
        if owns_transaction:
            database.conn.execute("COMMIT")
    except BaseException:
        if owns_transaction and database.conn.in_transaction:
            database.conn.execute("ROLLBACK")
        raise


def make_router(env):
    router = APIRouter(prefix="/api/admin/group-status", dependencies=[Depends(env["admin"])])

    def known():
        from reel.config import load_config

        return load_config(str(env["ROOT"] / "config.yaml"))["groups"]

    @router.get("")
    def listing():
        database = env["db"]()
        with closing(database.conn):
            done = completed_groups(database)
        return {"groups": [{"name": name, "completed": name in done} for name in known()]}

    @router.post("/{name}")
    async def change(name: str, request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                raise HTTPException(413, _t("account_api.request_too_large"))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, _t("access.invalid_request")) from None
        if (
            not isinstance(data, dict)
            or set(data) != {"completed"}
            or type(data["completed"]) is not bool
        ):
            raise HTTPException(400, _t("group_status.choose_completed_for_the_group_status"))
        if name not in known():
            raise HTTPException(404, _t("academy_api.group_not_found"))
        database = env["db"]()
        with closing(database.conn):
            principal = getattr(request.state, "principal", None) or {}
            set_completed(database, name, data["completed"], actor=principal.get("id"))
        return {"name": name, "completed": data["completed"]}

    return router
