"""Phone recording in the browser, built on the existing resumable upload.

A recording session is an ordinary upload folder ``out/uploads/<uid>/`` whose ``meta.json`` has
``kind: "recording"``. Its parts are created while the lecturer records (part 1, part 2 ...). The bytes
travel through the existing ``PUT /api/uploads/<uid>/<index>?offset=N`` route: MediaRecorder timeslice
chunks are buffered in the phone's IndexedDB and resent after a dropped connection. While a part is open,
its ``size`` in meta.json is only a byte cap. Closing the part records the final size.

Parts can be deleted until the lecturer confirms. Confirm turns the browser container (fragmented MP4 or
WebM) into a regular MP4/MKV with a stream copy, then hands the folder to the existing ``_finish_upload``
-> ``pipeline.enqueue`` path. The job is created exactly as for an uploaded lesson.

The module also holds the multi-file upload ordering helper used by ``POST /api/uploads``.
"""
from __future__ import annotations

from reel.i18n import t as _t

import asyncio
import json
import logging
import secrets
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks, APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from reel import lesson_dates, pipeline
from reel.web import accounts as account_auth

log = logging.getLogger("reel.web.recording")

MAX_PARTS = 30                      # interruptions (calls, screen lock) each start a new part
PART_CAP = 8 * 1024 ** 3            # one open part may grow to 8 GB (more than 4 h at 4 Mbit/s)
MIN_FREE = 5 * 1024 ** 3            # refuse to start or extend a recording below 5 GB free disk
MAX_DURATION = 6 * 3600
REMUX_TIMEOUT = 3600
FINAL_EXT = {".mp4": ".mp4", ".webm": ".mkv"}   # both are in pipeline.VIDEO_EXT


# --- multi-file upload order ---------------------------------------------------------------------
def ordered_disk_names(names: list[str]) -> list[str]:
    """Disk names that make ``pipeline.parts_of`` (natural name order) return the parts in ``names``
    order. Names already in natural order are kept unchanged; otherwise each gets a ``01 `` prefix."""
    names = list(names)
    if sorted(names, key=pipeline.natural_key) == names:
        return names
    width = max(2, len(str(len(names))))
    return [f"{i:0{width}d} {name}" for i, name in enumerate(names, 1)]


# --- the subject taught now ----------------------------------------------------------------------
def _day(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def subject_period(item: dict) -> tuple[date | None, date | None] | None:
    """The subject's teaching period when the group's schedule gives one, else None.

    config.yaml subjects have no dates yet; the keys below are the shapes the group schedule may use."""
    for start, end in (("start", "end"), ("start_date", "end_date"), ("starts", "ends"), ("from", "to")):
        if item.get(start) or item.get(end):
            return _day(item.get(start)), _day(item.get(end))
    period = item.get("period") or item.get("schedule")
    if isinstance(period, dict) and (period.get("start") or period.get("end")):
        return _day(period.get("start")), _day(period.get("end"))
    return None


def current_subject(group_cfg: dict, today: date, recent: str | None = None) -> str:
    """Default subject for a new lesson of the group.

    1. The subject whose schedule period contains today (the latest started one if several run).
    2. Else the subject of the group's most recent lesson.
    3. Else the group's first subject."""
    subjects = [s for s in group_cfg.get("subjects") or [] if isinstance(s, dict) and s.get("name")]
    running = []
    for s in subjects:
        period = subject_period(s)
        if period and (period[0] is None or period[0] <= today) and (period[1] is None or today <= period[1]):
            running.append((period[0] or date.min, s["name"]))
    if running:
        return max(running)[1]
    names = [s["name"] for s in subjects]
    if recent in names:
        return recent
    return names[0] if names else ""


# --- container fix-up ------------------------------------------------------------------------------
def remux(src: Path, dest: Path) -> bool:
    """Stream-copy a browser recording into a regular file (duration, index, faststart).

    Lossless and IO-bound. False when ffmpeg is missing or cannot read the part."""
    fmt = "mp4" if dest.suffix.lower() == ".mp4" else "matroska"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-fflags", "+genpts",
            "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
    if fmt == "mp4":
        args += ["-movflags", "+faststart"]
    try:
        subprocess.run(args + ["-f", fmt, str(dest)], check=True, capture_output=True, timeout=REMUX_TIMEOUT)
        return dest.is_file() and dest.stat().st_size > 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("remux of %s failed: %s", src.name, exc)
        dest.unlink(missing_ok=True)
        return False


# --- routes ----------------------------------------------------------------------------------------
def make_router(env):
    router = APIRouter(prefix="/api/recordings")
    uploader = env["uploader"]

    def root() -> Path:
        return env["ROOT"]

    def load_config() -> dict:
        from reel.config import load_config as load
        try:
            return load(str(root() / "config.yaml"))
        except Exception:  # noqa: BLE001
            raise HTTPException(503, _t('recording.settings_config_yaml_cannot_be_read')) from None

    def read_only(query: str, args: tuple):
        path = root() / "reel.db"
        if not path.exists():
            return None
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            return conn.execute(query, args).fetchone()

    def lesson_number(config: dict, group: str, subject: str) -> int:
        from reel.stages import next_lesson_number
        path = root() / "reel.db"
        if not path.exists():
            return 1
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            return next_lesson_number(SimpleNamespace(conn=conn), config, group, subject)

    def open_group(config: dict, group) -> dict:
        account_auth.require_group(group)
        if not isinstance(group, str) or group not in config["groups"]:
            raise HTTPException(400, _t('recording.choose_one_of_your_groups'))
        env["_require_open_group"](group)
        return config["groups"][group]

    def disk_ok() -> None:
        if shutil.disk_usage(root()).free < MIN_FREE:
            raise HTTPException(507, _t('recording.the_server_does_not_have_enough_free'))

    async def body(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, _t('account_api.request_too_large'))
        try:
            value = json.loads(raw or b"{}")
        except (ValueError, UnicodeError):
            value = None
        if not isinstance(value, dict):
            raise HTTPException(400, _t('access.invalid_request'))
        return value

    def lock(uid: str) -> asyncio.Lock:
        return env["_CHUNK_LOCKS"].setdefault(uid, asyncio.Lock())

    def folder(uid: str) -> Path:
        return env["_uploads"]() / uid

    def load_meta(uid: str) -> dict:
        meta = env["_upload_meta"](uid)               # 404 for unknown ids and symlinked folders
        if meta.get("kind") != "recording":
            raise HTTPException(404, _t('recording.recording_not_found'))
        account_auth.require_group(meta.get("group"))
        return meta

    def save_meta(uid: str, meta: dict) -> None:
        env["_atomic_json"](folder(uid) / "meta.json", meta)

    def part(meta: dict, index: int) -> dict:
        if not 0 <= index < len(meta["files"]) or meta["files"][index].get("deleted"):
            raise HTTPException(404, _t('recording.part_not_found'))
        return meta["files"][index]

    def received(uid: str, item: dict) -> int:
        path = folder(uid) / "parts" / item["name"]
        return path.stat().st_size if path.is_file() and not path.is_symlink() else 0

    def public(uid: str, meta: dict) -> dict:
        parts = [{"index": i, "received": received(uid, f), "open": bool(f.get("open")),
                  "size": None if f.get("open") else f["size"], "duration": f.get("duration"),
                  "started": f.get("started"), "interrupted": bool(f.get("interrupted"))}
                 for i, f in enumerate(meta["files"]) if not f.get("deleted")]
        return {"id": uid, "completed": False, "group": meta["group"], "subject": meta["subject"],
                "lesson": meta["lesson"], "lesson_number": meta.get("lesson_number"),
                "lesson_date": meta["lesson_date"], "mime": meta.get("mime"), "parts": parts,
                "chunk": env["UPLOAD_CHUNK"], "max_parts": MAX_PARTS}

    @router.get("/defaults")
    def defaults(group: str, subject: str | None = None, _: str = Depends(uploader)):
        """Form defaults: today's date, the subject taught now (the schedule), the next lesson number."""
        config = load_config()
        group_cfg = open_group(config, group)
        names = [s["name"] for s in group_cfg["subjects"]]
        recent = read_only("SELECT subject FROM jobs WHERE grp=? AND subject IS NOT NULL ORDER BY id DESC LIMIT 1",
                           (group,))
        with closing(env['db']().conn) as conn:
            row=conn.execute('SELECT subjects_json FROM group_metadata WHERE name=?',(group,)).fetchone()
        try:periods={item['name'].casefold():item for item in json.loads(row[0])} if row else {}
        except (ValueError,TypeError,KeyError):periods={}
        scheduled={**group_cfg,'subjects':[{**subject,**periods.get(subject['name'].casefold(),{})} for subject in group_cfg['subjects']]}
        now = current_subject(scheduled, lesson_dates.today(), recent[0] if recent else None)
        chosen = {n.casefold(): n for n in names}.get(str(subject or "").casefold(), now)
        return {"group": group, "subjects": names, "current": now, "subject": chosen,
                "number": lesson_number(config, group, chosen), "date": lesson_dates.today().isoformat()}

    @router.get("/info")
    def info(_: str = Depends(uploader)):
        """The page needs https for the camera; tell it the public https address when one is set."""
        url = account_auth.public_base(root())
        return {"public_url": url if url and url.startswith("https://") else None, "max_parts": MAX_PARTS}

    @router.post("")
    async def create(request: Request, who: str = Depends(uploader)):
        data = await body(request)
        config = load_config()
        group = data.get("group")
        group_cfg = open_group(config, group)
        subjects = {s["name"].casefold(): s["name"] for s in group_cfg["subjects"]}
        subject = subjects.get(str(data.get("subject") or "").casefold())
        if subject is None:
            raise HTTPException(400, _t('recording.choose_one_of_the_group_s_subjects'))
        number = data.get("lesson_number")
        if type(number) is not int or not 1 <= number <= 999:
            raise HTTPException(400, _t('recording.the_lesson_number_must_be_between_1'))
        mime = str(data.get("mime") or "").lower()[:120]
        ext = ".mp4" if mime.startswith("video/mp4") else ".webm" if mime.startswith("video/webm") else None
        if ext is None:
            raise HTTPException(400, _t('recording.the_browser_must_record_in_mp4_or'))
        disk_ok()
        env["_prune_uploads"]()
        uid = secrets.token_hex(10)                    # 20 hex chars, the upload id format
        (folder(uid) / "parts").mkdir(parents=True)
        meta = {"kind": "recording", "group": group, "subject": subject, "lesson": _t('app.lesson_number', number=number),
                "lesson_number": number, "lesson_date": lesson_dates.today().isoformat(),
                "lesson_date_changed": True, "files": [], "ext": ext, "mime": mime, "by": who,
                "created": time.time(), "uploader_id": (account_auth.PRINCIPAL.get() or {}).get("id"),
                "owner_token": request.session.setdefault("upload_owner",secrets.token_urlsafe(24))}
        save_meta(uid, meta)
        return public(uid, meta)

    @router.get("/{uid}")
    async def status(uid: str, _: str = Depends(uploader)):
        async with lock(uid):
            if receipt := env["_upload_receipt"](uid):
                return {"id": uid, "completed": True, "result": receipt}
            return public(uid, load_meta(uid))

    @router.post("/{uid}/parts")
    async def new_part(uid: str, _: str = Depends(uploader)):
        async with lock(uid):
            meta = load_meta(uid)
            if len(meta["files"]) >= MAX_PARTS:
                raise HTTPException(409, _t('recording.a_lesson_can_contain_at_most_count', count=MAX_PARTS))
            disk_ok()
            index = len(meta["files"])
            name = f"part-{index + 1:02d}{meta['ext']}"
            meta["files"].append({"name": name, "size": PART_CAP, "open": True, "started": time.time()})
            save_meta(uid, meta)
            return {"index": index, "name": name}

    @router.post("/{uid}/parts/{index}/close")
    async def close_part(uid: str, index: int, request: Request, _: str = Depends(uploader)):
        data = await body(request)
        size, duration = data.get("size"), data.get("duration")
        if type(size) is not int or size < 0:
            raise HTTPException(400, _t('recording.specify_the_final_part_size'))
        async with lock(uid):
            meta = load_meta(uid)
            item = part(meta, index)
            have = received(uid, item)
            if not item.get("open"):
                if item["size"] == size:
                    return {"ok": True, "size": size}  # a retried close
                raise HTTPException(409, _t('recording.this_part_is_already_closed'))
            if size != have:                           # the phone still holds unsent bytes
                return JSONResponse({"received": have}, status_code=409)
            if size == 0:
                item.update(open=False, deleted=True, size=0)
            else:
                item.update(open=False, size=size, closed=time.time(), interrupted=data.get("interrupted") is True)
                if isinstance(duration, (int, float)) and not isinstance(duration, bool) and 0 < duration <= MAX_DURATION:
                    item["duration"] = round(float(duration), 1)
            save_meta(uid, meta)
            return {"ok": True, "size": size}

    @router.delete("/{uid}/parts/{index}")
    async def delete_part(uid: str, index: int, _: str = Depends(uploader)):
        async with lock(uid):
            meta = load_meta(uid)
            item = part(meta, index)
            item.update(open=False, deleted=True, size=0)
            path = folder(uid) / "parts" / item["name"]
            if path.is_file() and not path.is_symlink():
                path.unlink()
            save_meta(uid, meta)
            return {"ok": True}

    def finish(uid: str, meta: dict, who: str):
        parts = folder(uid) / "parts"
        if not meta.get("confirmed"):
            live = [f for f in meta["files"] if not f.get("deleted")]
            final = []
            for n, item in enumerate(live, 1):
                src = parts / item["name"]
                ext = FINAL_EXT.get(src.suffix.lower(), ".mp4")
                temp = parts / f".fixed-{n:02d}{ext}"
                if not remux(src, temp):   # automatic fallback: keep the browser's bytes as they are
                    shutil.copyfile(src, temp)
                final.append((temp, f"part-{n:02d}{ext}", item))
            for path in list(parts.iterdir()):
                if not path.name.startswith(".fixed-"):
                    path.unlink()
            files = []
            for temp, name, item in final:
                temp.rename(parts / name)
                files.append({"name": name, "size": (parts / name).stat().st_size,
                              "duration": item.get("duration"), "recorded_bytes": item["size"]})
            meta.update(files=files, confirmed=time.time())
            save_meta(uid, meta)
        return env["_finish_upload"](uid, who)

    @router.post("/{uid}/confirm")
    async def confirm(uid: str, tasks: BackgroundTasks, who: str = Depends(uploader)):
        async with lock(uid):
            if receipt := env["_upload_receipt"](uid):
                return receipt
            meta = load_meta(uid)
            if not meta.get("confirmed"):
                live = [f for f in meta["files"] if not f.get("deleted")]
                if not live:
                    raise HTTPException(409, _t('recording.no_recording_parts_are_available'))
                if any(f.get("open") for f in live):
                    raise HTTPException(409, _t('recording.parts_are_still_uploading_wait_until_all'))
                for f in live:
                    if received(uid, f) != f["size"]:
                        raise HTTPException(409, _t('recording.one_part_has_not_been_received_in'))
            result = await run_in_threadpool(finish, uid, meta, who)
            if result.get('job_id'):
                from reel.web.job_features import process_review
                tasks.add_task(process_review,root(),result['job_id'])
            return result

    @router.delete("/{uid}")
    async def cancel(uid: str, _: str = Depends(uploader)):
        async with lock(uid):
            if env["_upload_receipt"](uid):
                raise HTTPException(409, _t('recording.this_lesson_has_already_been_submitted_for'))
            load_meta(uid)
            shutil.rmtree(folder(uid), ignore_errors=True)
            return {"ok": True}

    return router
