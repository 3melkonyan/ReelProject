"""Admin-only thumbnail style selection. Never calls group_style (GET must not write)."""

from __future__ import annotations

import io
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from reel import thumbnail
from reel.i18n import t as _t
from reel.web.admin import _CONFIG_LOCK, _load, require_admin

ROOT = Path(__file__).resolve().parents[2]
_LOCK = RLock()
router = APIRouter(prefix="/api/admin")


def _image_admin(request: Request):
    role = request.session.get("role")
    if role not in ("admin", "viewer", "uploader"):
        raise HTTPException(401, "login required")
    if role != "admin":
        raise HTTPException(403, "admin only")


def _read():
    path = ROOT / "thumbnail_styles.json"
    if path.is_symlink():
        raise HTTPException(409, "invalid styles file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise HTTPException(409, "thumbnail styles file is not readable") from None
    if not isinstance(data, dict):
        raise HTTPException(409, "invalid styles file")
    return data


@router.get("/thumbnail-styles", dependencies=[Depends(require_admin)])
def catalogue():
    with _CONFIG_LOCK, _LOCK:
        cfg = _load()
        stored = _read()
        used = {v for v in stored.values() if type(v) is int and 0 <= v < len(thumbnail.STYLES)}
        groups = {}
        for name in cfg["groups"]:
            value = stored.get(name)
            assigned = type(value) is int and 0 <= value < len(thumbnail.STYLES)
            if not assigned:
                value = next(
                    (i for i in range(len(thumbnail.STYLES)) if i not in used),
                    len(used) % len(thumbnail.STYLES),
                )
                used.add(value)
            groups[name] = {"style": value, "assigned": assigned}
        return {
            "styles": [
                {
                    "id": i,
                    "palette": palette,
                    "motif": motif,
                    "preview": f"/api/admin/thumbnail-styles/{i}/preview.png",
                }
                for i, (palette, motif) in enumerate(thumbnail.STYLES)
            ],
            "groups": groups,
        }


@router.put("/thumbnail-styles/{group}", dependencies=[Depends(require_admin)])
async def choose(group: str, request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "bad request") from None
    if (
        not isinstance(body, dict)
        or set(body) != {"style"}
        or type(body["style"]) is not int
        or not 0 <= body["style"] < len(thumbnail.STYLES)
    ):
        raise HTTPException(400, "invalid thumbnail style")
    with _CONFIG_LOCK, _LOCK:
        if group not in _load()["groups"]:
            raise HTTPException(404, "unknown group")
        path = ROOT / "thumbnail_styles.json"
        with thumbnail.styles_lock(path):  # the worker's group_style() writes it too
            data = _read()
            data[group] = body["style"]
            fd, name = tempfile.mkstemp(prefix=".thumbnail_styles-", dir=ROOT)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=1)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return {"group": group, "style": body["style"]}


@lru_cache(maxsize=18)
def _preview(style: int) -> bytes:
    from PIL import Image, ImageDraw

    with _LOCK:
        frame = Image.new("RGB", (640, 360), (32, 34, 36))
        draw = ImageDraw.Draw(frame)
        for y in range(40, 320, 55):
            draw.line((45, y, 550, y), fill=(100, 125, 115), width=2)
        image = thumbnail.render_card(
            frame, "Academy", _t("thumbnail_styles.lesson_1_new_knowledge"), style
        )
        image.thumbnail((640, 360))
        output = io.BytesIO()
        image.convert("RGB").save(output, "PNG", optimize=True)
        return output.getvalue()


@router.get("/thumbnail-styles/{style}/preview.png", dependencies=[Depends(_image_admin)])
def preview(style: int):
    if not 0 <= style < len(thumbnail.STYLES):
        raise HTTPException(404, "unknown style")
    return Response(
        _preview(style), media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )
