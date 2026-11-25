"""Admin-only settings API; configuration writes use the shared atomic writer."""
from __future__ import annotations

from reel.i18n import t as _t

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
        raise HTTPException(400, _t('admin.group_names_must_contain_1_60_letters'))
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
        raise HTTPException(500, _t('admin.settings_could_not_be_saved_check_disk')) from exc


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
        raise HTTPException(409, _t('admin.complete_or_resolve_these_lessons_first_ids') + ", ".join(map(str, ids)))


def _inbox(name: str) -> Path:
    """Never move or inspect an inbox symlink outside the selected root."""
    base = ROOT / "inbox"
    path = base / name
    if base.is_symlink() or path.is_symlink() or (base.exists() and not base.is_dir()) or (path.exists() and not path.is_dir()):
        raise HTTPException(409, _t('admin.the_inbox_path_is_not_a_regular'))
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
            raise HTTPException(400, _t('admin.invalid_playlist_url')) from exc
        if url.scheme not in ("http", "https") or host not in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be") or len(ids) != 1:
            raise HTTPException(400, _t('admin.provide_a_youtube_playlist_url_with_a'))
        value = ids[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HTTPException(400, _t('admin.playlist_ids_may_contain_only_latin_letters'))
    return value


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, _t('admin.expected_json_data')) from exc
    if not isinstance(body, dict):
        raise HTTPException(400, _t('admin.expected_a_json_object'))
    return body


@router.get("/config")
def config() -> dict:
    with _CONFIG_LOCK:
        return {"config": _load(), "refs": _refs()}



def _membership_conflict(database, name):
    row = database.conn.execute("SELECT count(*) FROM users WHERE deleted_at IS NULL AND "
                                "EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (name,)).fetchone()
    if row[0]:
        raise HTTPException(409, _t('admin.users_are_assigned_to_this_group_reassign'))


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
        raise HTTPException(400, _t('admin.the_subjects_slack_channel_and_lecturers_fields'))
    target = _group_name(body["rename_to"]) if "rename_to" in body else name
    group = {k: v for k, v in body.items() if k != "rename_to"}
    if "framing" in group and (not isinstance(group["framing"], str) or group["framing"] not in ("auto", "board", "projector", "off")):
        raise HTTPException(400, _t('admin.invalid_framing_mode_choose_one_of_the'))
    if not isinstance(group["lecturers"], list) or any(not isinstance(n, str) for n in group["lecturers"]):
        raise HTTPException(400, _t('admin.lecturers_must_be_a_list_of_reference'))
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
                raise HTTPException(404, _t('admin.the_group_to_rename_was_not_found'))
            if target in groups:
                raise HTTPException(409, _t('admin.a_group_with_this_name_already_exists'))
        if any(n not in _refs() for n in group["lecturers"]):
            raise HTTPException(400, _t('admin.choose_an_existing_lecturer_reference_name_or'))
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
                raise HTTPException(409, _t('admin.an_inbox_directory_with_the_new_name'))
            if source.exists():
                try:
                    source.rename(destination)
                    moved = True
                except OSError as exc:
                    raise HTTPException(409, _t('admin.the_inbox_directory_could_not_be_renamed')) from exc
        try:
            with _metadata_change(name, target, normalized["groups"][target]["subjects"]):
                saved = _save(normalized)
        except Exception:
            if moved:
                try:
                    destination.rename(source)
                except OSError as exc:
                    raise HTTPException(500, _t('admin.saving_settings_and_restoring_the_inbox_directory')) from exc
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
            raise HTTPException(404, _t('academy_api.group_not_found'))
        _job_conflict(_active_jobs(name))
        inbox = _inbox(name)
        if inbox.exists() and any(p.is_file() or p.is_symlink() for p in inbox.rglob("*")):
            raise HTTPException(409, _t('admin.the_group_s_inbox_contains_files_process'))
        if len(cfg["groups"]) == 1:
            raise HTTPException(409, _t('admin.you_cannot_delete_the_last_group'))
        del cfg["groups"][name]
        with _metadata_change(name, None):
            saved = _save(cfg)
        return {"ok": True, "config": saved}


def _put_settings(body: dict) -> dict:
    if not {"operator_slack_channel", "cut"} <= set(body) <= {"operator_slack_channel", "cut", "time_savings"} or not isinstance(body["cut"], dict) or set(body["cut"]) != {"min_silence_sec", "max_cut_ratio"}:
        raise HTTPException(400, _t('admin.expected_operator_slack_channel_and_cut_with'))
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
