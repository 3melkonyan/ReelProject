"""Lesson calendar dates, independent of upload/publication timestamps (Asia/Yerevan)."""
from __future__ import annotations

from reel.i18n import t as _t
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Yerevan')


def today() -> date:
    return datetime.now(ZONE).date()


def validate(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(_t('text.lesson_dates.enter_a_valid_lesson_date_in_yyyy'))
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(_t('text.lesson_dates.enter_a_valid_lesson_date')) from None
    if parsed > today():
        raise ValueError(_t('text.lesson_dates.the_lesson_date_cannot_be_in_the'))
    return parsed.isoformat()


def recording_date(probe: dict) -> str | None:
    """Prefer the phone's offset-bearing tag; ignore malformed/future camera dates."""
    if not isinstance(probe, dict):
        return None
    fmt = probe.get('format') or {}
    streams = probe.get('streams') or []
    tags = [fmt.get('tags', {}) if isinstance(fmt, dict) else {}]
    tags += [s.get('tags', {}) for s in streams if isinstance(s, dict)] if isinstance(streams, list) else []
    for key in ('com.apple.quicktime.creationdate', 'creation_time'):
        for collection in tags:
            if not isinstance(collection, dict):
                continue
            raw = collection.get(key)
            if not isinstance(raw, str):
                continue
            try:
                instant = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                day = instant.astimezone(ZONE).date() if instant.tzinfo else instant.date()
                return validate(day.isoformat())
            except (ValueError, OverflowError):
                continue
    return None


def job_date(job) -> str | None:
    """Old jobs have no known lesson date: use registration day, explicitly marked by the UI."""
    data = dict(job)
    try:
        if data.get('lesson_date'):
            return validate(data['lesson_date'])
        created = datetime.fromisoformat(str(data.get('created_at', '')).replace('Z', '+00:00'))
        if created.tzinfo is None:
            from datetime import timezone
            created = created.replace(tzinfo=timezone.utc)
        return created.astimezone(ZONE).date().isoformat()
    except (ValueError, OverflowError):
        return None


_LABELLED = re.compile(r'(?im)^([ \t]*(?:(?:Դասի|Նկարահանման)\s+ամսաթիվ(?:ը)?|Ամսաթիվ(?:ը)?|Lesson\s+date|Date)\s*[:՝–-]\s*)(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})(?=\s|$)')


def dated_metadata(metadata: dict, day: str) -> dict:
    """Replace existing date labels, never invent a date line or alter titles/topic dates."""
    day = validate(day)
    def replace(match):
        original = match[2]
        value = day
        if not original.startswith(day[:4]) and len(original) == 10 and original[2] in './':
            value = original[2].join(reversed(day.split('-')))
        return match[1] + value
    result = dict(metadata)
    for field in ('description', 'youtube_description'):
        if isinstance(result.get(field), str):
            result[field] = _LABELLED.sub(replace, result[field])
    return result
