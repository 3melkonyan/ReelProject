"""Own-account notification preferences; no app import or shared-login identity."""
from contextlib import closing
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from reel import student_mail
from reel.web import accounts


def make_router(env):
    router = APIRouter(prefix="/api/library")

    def reader(request: Request):
        who = env["authenticated"](request)
        if who not in ("admin", "viewer", "student", "lecturer"):
            raise HTTPException(403, "library only")
        principal = accounts.PRINCIPAL.get()
        return principal.get("id") if principal and not principal.get("legacy") else None

    @router.get("/notifications")
    def get_preferences(user_id=Depends(reader)):
        if user_id is None:
            return {"personal": False, "new_lessons": False, "weekly_digest": False}
        database = env["db"]()
        with closing(database.conn):
            try:
                return {"user_id": user_id, **student_mail.preferences(database, user_id)}
            except ValueError:
                raise HTTPException(403, "Հաշիվը հասանելի չէ։") from None

    @router.put("/notifications")
    async def put_preferences(request: Request, user_id=Depends(reader)):
        if request.headers.get("x-requested-with") != "reel":
            raise HTTPException(400, "bad request")
        if user_id is None:
            raise HTTPException(403, "Կարգավորումները հասանելի են միայն անձնական հաշվով։")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, "Հարցումը չափազանց մեծ է։")
        try:
            values = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Անվավեր հարցում։") from None
        if not isinstance(values, dict):
            raise HTTPException(400, "Անվավեր հարցում։")
        if "expected_user_id" in values:
            expected = values.pop("expected_user_id")
            if type(expected) is not int or expected != user_id:
                raise HTTPException(401, "Հաշիվը փոխվել է։ Խնդրում ենք կրկին մուտք գործել։")
        database = env["db"]()
        with closing(database.conn):
            try:
                return {"user_id": user_id, **student_mail.save_preferences(database, user_id, values)}
            except ValueError:
                raise HTTPException(400, "Նշեք միայն ծանուցումների միացման կամ անջատման արժեքներ։") from None

    return router
