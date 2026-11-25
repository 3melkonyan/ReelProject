"""Admin-only lecturer inspection and atomic per-job choices."""
from __future__ import annotations

from reel.i18n import t as _t

from contextlib import closing
import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from reel.db import DB
from reel.web.admin import require_admin

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter()
_IMAGE = re.compile(r"(?:identity_[1-6]|ref_\d{1,2})\.jpg", re.ASCII)
_MODES = {"auto", "chosen", "none", "unsure", "refs"}
_CHOICE_LOCK = threading.Lock()


def _image_admin(request: Request) -> str:
    """Images use session authentication only: img elements cannot send CSRF headers."""
    role = request.session.get("role")
    if role not in ("admin", "viewer", "uploader"):
        raise HTTPException(401, "login required")
    if role != "admin":
        raise HTTPException(403, "admin only")
    return role


def _workdir(job_id: int) -> Path:
    base = ROOT / "work"
    directory = base / str(job_id)
    if base.is_symlink() or directory.is_symlink():
        raise HTTPException(409, _t('lecturer_api.the_lesson_work_path_is_not_a'))
    return directory


def _auto_dir(directory: Path) -> Path:
    auto = directory / "auto_refs"
    if auto.is_symlink():
        raise HTTPException(409, _t('lecturer_api.the_lecturer_images_path_is_not_a'))
    return auto


def _regular(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _read_json(path: Path) -> dict | None:
    if not _regular(path):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: object) -> int | float | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _reference_images(auto: Path) -> list[str]:
    # The producer replaces auto_refs while blur runs; a missing snapshot is normal.
    try:
        return sorted((p.name for p in auto.iterdir()
                       if re.fullmatch(r"ref_\d{1,2}\.jpg", p.name, re.ASCII) and _regular(p)),
                      key=lambda n: int(n[4:-4]))
    except OSError:
        return []


def _choice(value: dict | None) -> dict | None:
    """Only return the public decision, never its stored biometric centre."""
    if value is None:
        return None
    if value.get("none") is True:
        return {"none": True}
    rank = value.get("identity")
    if type(rank) is int and 1 <= rank <= 6:
        return {"identity": rank}
    return None


def _job(database: DB, job_id: int):
    try:
        return database.job(job_id)
    except KeyError:
        raise HTTPException(404, _t('app.lesson_not_found')) from None


def _can_choose(database: DB, job_id: int, job, stage) -> bool:
    return (job["status"] in ("queued", "stopped")
            and (stage is None or stage["status"] != "done")
            and database.publication(job_id, "youtube") is None)


def _snapshot(job_id: int) -> dict:
    database = DB(ROOT / "reel.db")
    try:
        job = _job(database, job_id)
        stage = database.stage(job_id, "blur")
        can_choose = _can_choose(database, job_id, job, stage)
        try:
            output = database.stage_output(job_id, "blur") or {}
        except ValueError:
            output = {}
        if not isinstance(output, dict):
            output = {}
        directory = _workdir(job_id)
        auto = _auto_dir(directory)
        report = _read_json(auto / "auto.json") or {}
        mode = report.get("mode")
        if not isinstance(mode, str) or mode not in _MODES:
            mode = output.get("lecturer_mode")
        if not isinstance(mode, str) or mode not in _MODES:
            mode = None
        summary = output.get("lecturer")
        if not isinstance(summary, dict):
            summary = {}
        reason = report.get("reason") or summary.get("reason") or (stage["error"] if stage else None)
        raw_identities = report.get("identities")
        if not isinstance(raw_identities, list):
            raw_identities = []
        identities = []
        for rank, raw in enumerate(raw_identities[:6], 1):
            raw = raw if isinstance(raw, dict) else {}
            image = f"identity_{rank}.jpg"
            identities.append({"rank": rank,
                               **{k: _number(raw.get(k)) for k in ("presence", "share", "face_px", "first_t", "last_t")},
                               "image": image if _regular(auto / image) else None})
        refs = _reference_images(auto)
        lecturers = output.get("lecturers")
        lecturers = [n for n in lecturers if isinstance(n, str)] if isinstance(lecturers, list) else []
        rank = report.get("lecturer_rank", summary.get("lecturer_rank"))
        blur_status = stage["status"] if stage and stage["status"] in ("running", "done", "failed") else "pending"
        return {"job_status": job["status"], "blur_status": blur_status, "mode": mode,
                "reason": reason if isinstance(reason, str) else None, "identities": identities,
                "lecturer_rank": rank if type(rank) is int and rank >= 1 else None,
                "refs": refs, "lecturers": lecturers,
                "choice": _choice(_read_json(directory / "lecturer_choice.json")),
                "can_choose": can_choose}
    finally:
        database.conn.close()


@router.get("/api/jobs/{job_id}/lecturer")
def lecturer(job_id: int, response: Response, _: str = Depends(require_admin)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _snapshot(job_id)


@router.get("/api/jobs/{job_id}/lecturer/{name:path}")
def image(job_id: int, name: str, _: str = Depends(_image_admin)) -> Response:
    if not _IMAGE.fullmatch(name):
        raise HTTPException(400, _t('lecturer_api.invalid_image_name'))
    database = DB(ROOT / "reel.db")
    try:
        _job(database, job_id)
    finally:
        database.conn.close()
    path = _auto_dir(_workdir(job_id)) / name
    if not _regular(path):
        raise HTTPException(404, _t('lecturer_api.image_not_found'))
    try:
        content = path.read_bytes()
    except OSError:
        raise HTTPException(404, _t('lecturer_api.image_not_found')) from None
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _centre(report: dict, rank: int) -> list[float]:
    centres = report.get("centres")
    if not isinstance(centres, list) or rank > len(centres):
        raise HTTPException(409, _t('lecturer_api.recognition_data_for_the_selected_person_is'))
    vector = centres[rank - 1]
    if not isinstance(vector, list) or len(vector) != 128 or any(_number(v) is None for v in vector):
        raise HTTPException(409, _t('lecturer_api.recognition_data_for_the_selected_person_is_2'))
    return [float(v) for v in vector]


def _write_choice(directory: Path, value: dict) -> None:
    from reel.lecturer_review import write_choice
    write_choice(directory, value)


def _save_choice(job_id: int, body: dict) -> dict:
    from reel import lecturer_review
    with closing(DB(ROOT / 'reel.db').conn) as conn:
        from types import SimpleNamespace
        database = SimpleNamespace(conn=conn)
        if lecturer_review.row(database,job_id):
            from reel.web.accounts import PRINCIPAL
            return lecturer_review.answer(database,ROOT,job_id,body,(PRINCIPAL.get() or {}).get('id'),'admin')
    if len(body) != 1 or next(iter(body)) not in ("identity", "none", "clear"):
        raise HTTPException(400, _t('lecturer_api.choose_one_action_identity_none_or_clear'))
    action = next(iter(body))
    if action == "identity":
        if type(body[action]) is not int or not 1 <= body[action] <= 6:
            raise HTTPException(400, _t('lecturer_api.the_person_number_must_be_an_integer'))
    elif body[action] is not True:
        raise HTTPException(400, _t('lecturer_api.the_value_for_none_or_clear_must'))
    with _CHOICE_LOCK:
        database = DB(ROOT / "reel.db")
        try:
            # The worker writes job/stage state before running blur. Reserve the SQLite
            # writer until the file commit, so queued -> running cannot race the decision.
            database.conn.execute("BEGIN IMMEDIATE")
            job = _job(database, job_id)
            stage = database.stage(job_id, "blur")
            if not _can_choose(database, job_id, job, stage):
                raise HTTPException(409, _t('lecturer_api.you_cannot_change_the_selection_now_the'))
            directory = _workdir(job_id)
            if action == "clear":
                (directory / "lecturer_choice.json").unlink(missing_ok=True)
                return {"ok": True, "choice": None}
            value = {"by": "admin", "at": datetime.now(timezone.utc).isoformat()}
            if action == "identity":
                report = _read_json(_auto_dir(directory) / "auto.json") or {}
                identities = report.get("identities")
                rank = body["identity"]
                if not isinstance(identities, list) or rank > min(6, len(identities)):
                    raise HTTPException(400, _t('lecturer_api.no_person_with_this_number_exists_refresh'))
                value.update(identity=rank, centre=_centre(report, rank))
            else:
                value["none"] = True
            _write_choice(directory, value)
            return {"ok": True, "choice": _choice(value)}
        except OSError as exc:
            raise HTTPException(500, _t('lecturer_api.the_selection_could_not_be_saved_try')) from exc
        finally:
            database.conn.rollback()  # No DB records changed; release the short writer reservation.
            database.conn.close()


@router.post("/api/jobs/{job_id}/lecturer")
async def choose_lecturer(job_id: int, request: Request, _: str = Depends(require_admin)) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, _t('admin.expected_a_json_object')) from None
    if not isinstance(body, dict):
        raise HTTPException(400, _t('admin.expected_a_json_object'))
    return await run_in_threadpool(_save_choice, job_id, body)
