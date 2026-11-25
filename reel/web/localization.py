"""Select a dashboard language without translating user-provided lesson content."""

import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from reel.config import load_config
from reel.i18n import DEFAULT_LANGUAGE, LANGUAGES, catalog, current_language, language_context, t


def configured_language(root: Path) -> str:
    try:
        return load_config(str(root / "config.yaml")).get("ui_language", DEFAULT_LANGUAGE)
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE


def install(app, root: Path, static: Path) -> None:
    @app.middleware("http")
    async def select_language(request: Request, call_next):
        language = request.cookies.get("reel-language")
        if language not in LANGUAGES:
            language = configured_language(root)
        with language_context(language):
            return await call_next(request)

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def entrypoint():
        html = (static / "index.html").read_text(encoding="utf-8")
        html = html.replace("__REEL_NOSCRIPT__", t("app.noscript")).replace(
            'lang="hy"', 'lang="' + current_language() + '"', 1
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        data = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))
        data.update(name=t("app.manifest_name"), lang=current_language())
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/i18n-data.js", include_in_schema=False)
    def browser_messages():
        language = current_language()
        data = json.dumps(
            {"language": language, "messages": dict(catalog(language))}, ensure_ascii=True
        )
        return Response(
            "window.REEL_I18N = " + data + ";\n",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )
