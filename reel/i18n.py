"""Dependency-free messages shared by the dashboard and email renderers."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

LANGUAGES = ("en", "hy")
DEFAULT_LANGUAGE = "en"
_language: ContextVar[str] = ContextVar("reel_ui_language", default=DEFAULT_LANGUAGE)


def validate_language(language: str) -> str:
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError("ui_language must be 'en' or 'hy'")
    return language


def current_language() -> str:
    return _language.get()


@contextmanager
def language_context(language: str) -> Iterator[None]:
    """Scope a request or background email without affecting concurrent work."""
    token = _language.set(validate_language(language))
    try:
        yield
    finally:
        _language.reset(token)


@lru_cache(maxsize=len(LANGUAGES))
def catalog(language: str) -> Mapping[str, str]:
    language = validate_language(language)
    path = Path(__file__).with_name("locales") / f"{language}.json"
    messages = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(messages, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                             for k, v in messages.items()):
        raise ValueError(f"Invalid {language} message catalog")
    return MappingProxyType(messages)


def t(key: str, *, language: str | None = None, **values: object) -> str:
    """Look up a message; missing keys and interpolation values fail visibly."""
    text = catalog(current_language() if language is None else language)[key]
    return text.format_map(values)
