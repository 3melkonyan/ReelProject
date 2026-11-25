"""Select a dashboard language without translating user-provided lesson content."""
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

from reel.config import load_config
from reel.i18n import DEFAULT_LANGUAGE, LANGUAGES, catalog, language_context
import json


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

    @app.get("/i18n-data.js", include_in_schema=False)
    def browser_messages():
        from reel.i18n import current_language
        language = current_language()
        data = json.dumps({"language": language, "messages": dict(catalog(language))}, ensure_ascii=True)
        return Response("window.REEL_I18N = " + data + ";\n", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})
