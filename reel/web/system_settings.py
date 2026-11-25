"""General administrator settings; no configuration, database or secret reads at import."""

from reel.i18n import t as _t
from contextlib import closing
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from reel import thumbnail
from reel.config import load_config
from reel.web import accounts

DEFAULT_STYLE_KEY = "default_thumbnail_style"


def _style(database):
    row = database.conn.execute("SELECT value FROM account_settings WHERE key=?", (DEFAULT_STYLE_KEY,)).fetchone()
    try:
        value = int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value < len(thumbnail.STYLES) else 0


def _check_admin(database, principal):
    row = database.conn.execute("SELECT role,active,removed_at,deleted_at FROM users WHERE id=?", (principal["id"],)).fetchone()
    if not row or row["role"] != "admin" or not row["active"] or row["removed_at"] or row["deleted_at"]:
        raise HTTPException(403, _t('system_settings.only_administrators_can_access_settings'))


def _snapshot(database, root):
    try:
        cfg = accounts.settings(root)
        port = int(cfg.get("SMTP_PORT") or 587)
        configured = all(cfg.get(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")) and 1 <= port <= 65535
    except (OSError, TypeError, ValueError):
        configured = False
    try:
        public_url = accounts.public_base(root)
    except OSError:
        public_url = None
    try:
        config = load_config(str(root / "config.yaml"))
        advanced = {"operator_slack_channel": config["operator_slack_channel"],
                    "cut": {key: config["cut"][key] for key in ("min_silence_sec", "max_cut_ratio")},
                    "time_savings": {key: config.get("time_savings", {}).get(key, default)
                                     for key, default in (("editing_min_per_hour", 30), ("publishing_min_per_lesson", 5))}}
    except (OSError, ValueError, KeyError, TypeError):
        advanced = None
    return {"email": {"configured": bool(configured)}, "public_url": public_url, "advanced": advanced,
            "legacy_enabled": accounts.legacy_enabled(database), "default_thumbnail_style": _style(database),
            "thumbnail_styles": [{"id": index, "preview": f"/api/admin/thumbnail-styles/{index}/preview.png"}
                                 for index in range(len(thumbnail.STYLES))]}


def make_router(env):
    router = APIRouter(prefix="/api/admin")

    def administrator(request: Request):
        env["admin"](request)
        principal = getattr(request.state, "principal", None)
        if not principal or type(principal.get("id")) is not int or principal.get("legacy"):
            raise HTTPException(401, _t('system_settings.sign_in_with_a_personal_account'))
        return principal

    @router.get("/system-settings")
    def get_settings(principal=Depends(administrator)):
        database = env["db"]()
        with closing(database.conn):
            _check_admin(database, principal)
            return _snapshot(database, env["ROOT"])

    @router.put("/system-settings")
    async def put_settings(request: Request, principal=Depends(administrator)):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, _t('account_api.request_too_large'))
        try:
            values = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, _t('access.invalid_request')) from None
        if (not isinstance(values, dict) or set(values) != {"default_thumbnail_style"}
                or type(values["default_thumbnail_style"]) is not int
                or not 0 <= values["default_thumbnail_style"] < len(thumbnail.STYLES)):
            raise HTTPException(400, _t('system_settings.choose_one_of_the_available_thumbnail_designs'))
        database = env["db"]()
        with closing(database.conn):
            database.conn.execute("BEGIN IMMEDIATE")
            try:
                _check_admin(database, principal)
                database.conn.execute("INSERT INTO account_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                      (DEFAULT_STYLE_KEY, str(values["default_thumbnail_style"])))
                database.conn.execute("COMMIT")
            except BaseException:
                if database.conn.in_transaction:
                    database.conn.execute("ROLLBACK")
                raise
            return _snapshot(database, env["ROOT"])

    return router
