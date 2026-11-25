"""Admin-only settings API; configuration writes use the shared atomic writer."""
from __future__ import annotations

from contextlib import contextmanager
import re
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from reel.config import load_config, save_config, validate_config
from reel.db import DB
from reel.publish import youtube
from reel.publish.errors import PublishError

ROOT = Path(__file__).resolve().parents[2]
_CONFIG_LOCK = threading.Lock()


def require_admin(request: Request) -> str:
    """Apply the same session and CSRF policy as the main app without importing it."""
    role = request.session.get("role")
    if role not in ("admin", "viewer", "uploader"):
        raise HTTPException(401, "login required")
    if role != "admin":
        raise HTTPException(403, "admin only")
    if request.headers.get("x-requested-with") != "reel":
        raise HTTPException(400, "bad request")
    return role


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _group_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[^\W_][\w .-]{0,59}", value):
        raise HTTPException(400, "Խմբի անունը՝ 1–60 տառ/թիվ, բացատ, '-', '.', '_'․ սկսիր տառով կամ թվով։")
    return value


def _load() -> dict:
    try:
        return load_config(str(ROOT / "config.yaml"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _validate(config: dict) -> dict:
    try:
        return validate_config(config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _save(config: dict) -> dict:
    try:
        return save_config(config, str(ROOT / "config.yaml"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "Չհաջողվեց պահպանել կարգավորումները։ Ստուգիր սկավառակը և գրելու իրավունքը։") from exc


def _refs() -> dict[str, int]:
    base = ROOT / "refs"
    if not base.is_dir() or base.is_symlink():
        return {}
    return {
        directory.name: sum(p.is_file() and not p.is_symlink() and p.suffix.lower() == ".jpg"
                            for p in directory.iterdir())
        for directory in sorted(base.iterdir())
        if directory.is_dir() and not directory.is_symlink()
    }


def _active_jobs(group: str, subjects: set[str] | None = None) -> list[int]:
    """An unassigned job must not silently inherit a replacement subject."""
    database = DB(ROOT / "reel.db")
    try:
        return [job["id"] for job in database.jobs(limit=-1)
                if job["grp"] == group and job["status"] != "done"
                and (subjects is None or not job["subject"] or job["subject"] in subjects)]
    finally:
        database.conn.close()


def _job_conflict(ids: list[int]) -> None:
    if ids:
        raise HTTPException(409, "Նախ ավարտիր կամ լուծիր այս դասերը․ ID-ներ՝ " + ", ".join(map(str, ids)))


def _inbox(name: str) -> Path:
    """Never move or inspect an inbox symlink outside the selected root."""
    base = ROOT / "inbox"
    path = base / name
    if base.is_symlink() or path.is_symlink() or (base.exists() and not base.is_dir()) or (path.exists() and not path.is_dir()):
        raise HTTPException(409, "Inbox-ի ուղին սովորական պանակ չէ․ դիմիր օպերատորին։")
    return path


def _playlist(value: object) -> object:
    if not isinstance(value, str):
        return value  # The shared validator supplies the field-specific type error.
    value = value.strip()
    if not value:
        return ""   # no playlist yet: allowed, the lesson is then published without one
    candidate = value if "://" in value else "https://" + value
    if "://" in value or value.startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/", "youtu.be/")):
        try:
            url = urlsplit(candidate)
            host = (url.hostname or "").lower()
            ids = parse_qs(url.query).get("list", [])
        except ValueError as exc:
            raise HTTPException(400, "Playlist-ի հղումը սխալ է։") from exc
        if url.scheme not in ("http", "https") or host not in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be") or len(ids) != 1:
            raise HTTPException(400, "Տուր YouTube playlist-ի հղումը՝ list= արժեքով, կամ playlist ID-ն։")
        value = ids[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(400, "Playlist ID-ն կարող է պարունակել միայն լատինատառեր, թվեր, '-' և '_'։")
    return value


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Սպասվում է JSON տվյալ։") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "Սպասվում է JSON օբյեկտ։")
    return body


@router.get("/config")
def config() -> dict:
    with _CONFIG_LOCK:
        return {"config": _load(), "refs": _refs()}



def _membership_conflict(database, name):
    row = database.conn.execute("SELECT count(*) FROM users WHERE deleted_at IS NULL AND "
                                "EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (name,)).fetchone()
    if row[0]:
        raise HTTPException(409, "Խմբին կցված օգտատերեր կան։ Վերանվանումից կամ ջնջումից առաջ փոխեք նրանց խմբերը։ Պատմությունը պահպանվում է։")


@contextmanager
def _metadata_change(name, target, subjects=None):
    from reel.web.group_management import reconcile_metadata
    database = DB(ROOT / "reel.db")
    try:
        database.conn.execute('BEGIN IMMEDIATE')
        if target != name:
            _membership_conflict(database, name)
        reconcile_metadata(database, name, target, subjects)
        yield
        database.conn.execute('COMMIT')
    except BaseException:
        if database.conn.in_transaction:
            database.conn.execute('ROLLBACK')
        raise
    finally:
        database.conn.close()


def _put_group(name: str, body: dict) -> dict:
    name = _group_name(name)
    allowed = {"subjects", "slack_channel", "lecturers", "classroom_course_id", "rename_to", "framing", "framing_correction", "processing"}
    if not {"subjects", "slack_channel", "lecturers"} <= body.keys() or body.keys() - allowed:
        raise HTTPException(400, "Պարտադիր են subjects, slack_channel, lecturers դաշտերը։ Անհայտ դաշտեր մի ուղարկիր։")
    target = _group_name(body["rename_to"]) if "rename_to" in body else name
    group = {k: v for k, v in body.items() if k != "rename_to"}
    if "framing" in group and (not isinstance(group["framing"], str) or group["framing"] not in ("auto", "board", "projector", "off")):
        raise HTTPException(400, "Կադրի ռեժիմը սխալ է։ Ընտրեք առաջարկված տարբերակներից։")
    if not isinstance(group["lecturers"], list) or any(not isinstance(n, str) for n in group["lecturers"]):
        raise HTTPException(400, "lecturers-ը պետք է լինի նմուշների անունների ցանկ կամ դատարկ ցանկ։")
    if isinstance(group["subjects"], list):
        group["subjects"] = [dict(item, playlist=_playlist(item["playlist"]))
                             if isinstance(item, dict) and "playlist" in item else item
                             for item in group["subjects"]]
    with _CONFIG_LOCK:
        cfg = _load()
        groups = cfg["groups"]
        old = groups.get(name)
        if target != name:
            if old is None:
                raise HTTPException(404, "Վերանվանվող խումբը չի գտնվել։")
            if target in groups:
                raise HTTPException(409, "Այդ անունով խումբ արդեն կա։")
        if any(n not in _refs() for n in group["lecturers"]):
            raise HTTPException(400, "Դասախոսին ընտրիր գոյություն ունեցող refs անուններից կամ ընտրիր «Ավտոմատ»։")
        if old is not None and "framing" in old:      # Preserve the setting for older clients that omit it.
            group.setdefault("framing", old["framing"])
        if old is not None and "framing_correction" in old:
            group.setdefault("framing_correction", old["framing_correction"])
        if old is not None:
            del groups[name]
        groups[target] = group
        normalized = _validate(cfg)
        if old is not None:
            removed = {s["name"] for s in old["subjects"]} - {s["name"] for s in normalized["groups"][target]["subjects"]}
            if target != name:
                _job_conflict(_active_jobs(name))
            elif removed:
                _job_conflict(_active_jobs(name, removed))
        moved = False
        if target != name:
            source, destination = _inbox(name), _inbox(target)
            if destination.exists():
                raise HTTPException(409, "Նոր անունով inbox պանակ արդեն կա․ նախ դիմիր օպերատորին։")
            if source.exists():
                try:
                    source.rename(destination)
                    moved = True
                except OSError as exc:
                    raise HTTPException(409, "Չհաջողվեց վերանվանել inbox պանակը։ Կարգավորումները չեն փոխվել։") from exc
        try:
            with _metadata_change(name, target, normalized["groups"][target]["subjects"]):
                saved = _save(normalized)
        except Exception:
            if moved:
                try:
                    destination.rename(source)
                except OSError as exc:
                    raise HTTPException(500, "Պահպանումն ու inbox-ի հետադարձ տեղափոխումը ձախողվեցին․ դիմիր օպերատորին։") from exc
            raise
        return {"ok": True, "name": target, "config": saved}


# The path converter lets invalid slash-containing names receive a useful HTTP 400.
@router.put("/groups/{name:path}")
async def put_group(name: str, request: Request) -> dict:
    return await run_in_threadpool(_put_group, name, await _body(request))


@router.delete("/groups/{name:path}")
def delete_group(name: str) -> dict:
    name = _group_name(name)
    with _CONFIG_LOCK:
        cfg = _load()
        if name not in cfg["groups"]:
            raise HTTPException(404, "Խումբը չի գտնվել։")
        _job_conflict(_active_jobs(name))
        inbox = _inbox(name)
        if inbox.exists() and any(p.is_file() or p.is_symlink() for p in inbox.rglob("*")):
            raise HTTPException(409, "Խմբի inbox-ում ֆայլեր կան․ նախ մշակիր կամ տեղափոխիր դրանք։")
        if len(cfg["groups"]) == 1:
            raise HTTPException(409, "Վերջին խումբը ջնջել չի կարելի։")
        del cfg["groups"][name]
        with _metadata_change(name, None):
            saved = _save(cfg)
        return {"ok": True, "config": saved}


def _put_settings(body: dict) -> dict:
    if not {"operator_slack_channel", "cut"} <= set(body) <= {"operator_slack_channel", "cut", "time_savings"} or not isinstance(body["cut"], dict) or set(body["cut"]) != {"min_silence_sec", "max_cut_ratio"}:
        raise HTTPException(400, "Սպասվում են operator_slack_channel և cut՝ min_silence_sec, max_cut_ratio դաշտերով։")
    with _CONFIG_LOCK:
        cfg = _load()
        cfg["operator_slack_channel"] = body["operator_slack_channel"]
        cfg["cut"].update(body["cut"])
        if "time_savings" in body:
            cfg["time_savings"] = body["time_savings"]
        return {"ok": True, "config": _save(cfg)}


@router.put("/settings")
async def put_settings(request: Request) -> dict:
    return await run_in_threadpool(_put_settings, await _body(request))


@router.get("/youtube/playlists")
def playlists() -> list[dict]:
    try:
        return youtube.list_playlists()
    except PublishError as exc:
        raise HTTPException(502, str(exc)) from exc
