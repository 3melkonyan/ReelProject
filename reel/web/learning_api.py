"""G-40/3 library API: filter options, lesson player data, search, watch progress and statistics."""
from __future__ import annotations

from contextlib import closing
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from reel import learning
from reel.insights import library_records, video_id
from reel.library_view import library_entries
from reel.web import accounts as account_auth


def make_router(env):
    router = APIRouter(prefix="/api")

    def library_reader(request: Request) -> str:
        who = env["authenticated"](request)
        if who not in ("admin", "viewer", "student", "lecturer"):
            raise HTTPException(403, "library only")
        return who

    def config_groups() -> dict:
        from reel.config import load_config
        try:
            return load_config(str(env["ROOT"] / "config.yaml"))["groups"]
        except Exception:  # noqa: BLE001 - an invalid config must not hide published lessons
            return {}

    def records(database):
        return library_entries(database, env["ROOT"] / "library.jsonl", groups=account_auth.groups())

    def user_id() -> int | None:
        """Personal account id; shared (legacy) logins have none, so their progress stays in the browser."""
        principal = account_auth.PRINCIPAL.get()
        return principal.get("id") if principal and not principal.get("legacy") else None

    def visible(database) -> dict[int, dict]:
        """Published lessons this viewer may open, by job id (demo/foreign ids are skipped)."""
        return {r["job_id"]: r for r in library_records(database, env["ROOT"] / "library.jsonl",
                 groups=account_auth.groups()) if isinstance(r.get("job_id"), int)}

    @router.get("/library/facets")
    def facets(_: str = Depends(library_reader)):
        from reel.web.group_status import completed_groups
        database = env["db"]()
        with closing(database.conn):
            from reel.watch_statistics import available
            return {**learning.library_facets(records(database), config_groups(), completed_groups(database),
                                             account_auth.groups(), database),
                    "watch_stats_available": available(database, account_auth.PRINCIPAL.get())}

    @router.get("/library/search")
    def library_search(q: str = "", _: str = Depends(library_reader)):
        """Search inside lessons (speech + board/slide OCR). Every hit carries its final-video time."""
        if len(q) > 200:
            raise HTTPException(400, "Որոնման տեքստը չափազանց երկար է։")
        database = env["db"]()
        with closing(database.conn):
            lessons = visible(database)
            learning.ensure_cues(database, lessons, env["WORK"])
            found = learning.search(database, q, lessons)
        for item in found["results"]:
            lesson = lessons[item["job_id"]]
            item.update({key: lesson.get(key) for key in ("title", "group", "subject", "lesson_no", "url",
                                                          "thumbnail", "lesson_date")})
            for hit in item["hits"]:
                hit["url"] = f"{lesson['url']}?t={int(hit['t'])}"
        return found

    @router.get("/library/progress")
    def my_progress(_: str = Depends(library_reader)):
        """The viewer's own progress on lessons they can still open ("Continue watching", card bars)."""
        uid = user_id()
        if uid is None:
            return {"personal": False, "items": []}
        database = env["db"]()
        with closing(database.conn):
            items = learning.progress_for(database, uid, set(visible(database)))
        return {"personal": True, "items": sorted(items.values(), key=lambda x: x["updated_at"], reverse=True)}

    @router.get("/library/{job_id}")
    def lesson(job_id: int, _: str = Depends(library_reader)):
        """One published lesson for the player: notes, synced transcript, chapters and the viewer's progress."""
        database = env["db"]()
        with closing(database.conn):
            found = visible(database).get(job_id)
            if found is None:
                raise HTTPException(404, "Դասը չի գտնվել։")
            notes = learning.public_summary(database.stage_output(job_id, "summary"))
            learning.ensure_cues(database, [job_id], env["WORK"])
            cues = learning.lesson_cues(database, job_id)
            uid = user_id()
            progress = learning.progress_for(database, uid, {job_id}).get(job_id) if uid is not None else None
        return {**found, "video_id": video_id(found["url"]), "summary": notes, "cues": cues, "progress": progress,
                "personal": uid is not None}

    @router.post("/library/{job_id}/progress")
    async def save_progress(job_id: int, request: Request, _: str = Depends(library_reader)):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 64 * 1024:
                raise HTTPException(413, "Հարցումը չափազանց մեծ է։")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Անվավեր հարցում։") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "Անվավեր հարցում։")
        uid = user_id()
        database = env["db"]()
        with closing(database.conn):
            found = visible(database).get(job_id)
            if found is None:
                raise HTTPException(404, "Դասը չի գտնվել։")
            if uid is None:
                return {"saved": False}
            expected = body.get("expected_user_id")
            if "expected_user_id" in body and (type(expected) is not int or expected != uid):
                raise HTTPException(401, "Հաշիվը փոխվել է։ Նորից մուտք գործեք։")
            database.conn.execute("BEGIN IMMEDIATE")
            try:
                row = database.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not row or not row["active"] or row["session_version"] != request.session.get("session_version"):
                    raise HTTPException(401, "Սեսիան ավարտվել է։ Կրկին մուտք գործեք։")
                if row["role"] != "admin" and found["group"] not in json.loads(row["groups_json"]):
                    raise HTTPException(404, "Դասը չի գտնվել։")
                result = learning.record_progress(database, uid, job_id, body.get("position", 0),
                                                  body.get("duration"), body.get("intervals", []),
                                                  known_duration=found.get("duration"))
                database.conn.execute("COMMIT")
            except ValueError:
                database.conn.execute("ROLLBACK")
                raise HTTPException(400, "Դիտման տվյալներն անվավեր են։ Խնդրում ենք կրկին բացել դասը։") from None
            except BaseException:
                if database.conn.in_transaction:
                    database.conn.execute("ROLLBACK")
                raise
        return {"saved": True, **result}

    return router
