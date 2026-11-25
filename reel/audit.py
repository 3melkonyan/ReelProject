"""Secret-free append-only audit events, with an explicit database context."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re

_DATABASE = ContextVar('reel_audit_database', default=None)
_ACTOR = ContextVar('reel_audit_actor', default=None)
_ALLOWED = frozenset(('role', 'groups', 'active', 'before', 'after', 'field', 'fields', 'subjects',
    'ta_ids', 'lecturer_ids', 'course_start', 'course_end', 'start', 'end', 'completed', 'status',
    'target', 'group', 'subject', 'job_id', 'user_id', 'invite_id', 'count', 'demo', 'reason',
    'lecturer_id', 'manual', 'end_date', 'name', 'schedule', 'ta', 'removed', 'deleted'))
_SENSITIVE = re.compile(r'password|secret|token|authorization|cookie|email|url|link|otp|code', re.I)
_VALUE_SECRET = re.compile(r'https?://|[\w.+-]+@[\w.-]+|xox[baprs]-|AIza|Bearer\s|[A-Za-z0-9_-]{32,}', re.I)


@contextmanager
def audit_context(database, actor=None):
    """Bind a DB instance; callers never depend on cwd or a production DB singleton."""
    db_token = _DATABASE.set(database)
    actor_token = _ACTOR.set(actor)
    try:
        yield
    finally:
        _ACTOR.reset(actor_token)
        _DATABASE.reset(db_token)


def _text(value, limit=180):
    value = str(value).replace('\x00', '')
    return '[REDACTED]' if _VALUE_SECRET.search(value) else value[:limit]


def clean_details(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {str(key): clean_details(item, depth + 1) for key, item in list(value.items())[:40]
                if str(key) in _ALLOWED and not _SENSITIVE.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [clean_details(item, depth + 1) for item in value[:100]]
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float('inf') else None
    return _text(value)


def write_event(database, actor=None, action=None, target=None, details=None, demo=False):
    """Append through the caller's connection, participating in its transaction."""
    if not isinstance(action, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,59}', action):
        raise ValueError('Invalid audit action')
    if actor is None:
        actor = _ACTOR.get()
    if isinstance(actor, dict):
        actor = actor.get('id')
    if actor is not None and (type(actor) is not int or actor < 1):
        actor = None
    cur = database.conn.execute(
        'INSERT INTO audit_events(created_at,actor,action,target,details,demo) VALUES(?,?,?,?,?,?)',
        (datetime.now(timezone.utc).isoformat(timespec='seconds'), actor, action, _text(target or ''),
         json.dumps(clean_details(details or {}), ensure_ascii=False, allow_nan=False), int(bool(demo))))
    return cur.lastrowid


def audit(actor, action, target, details):
    """Public four-argument seam; use inside ``with audit_context(database):``."""
    database = _DATABASE.get()
    if database is None:
        raise RuntimeError('audit() requires audit_context(database)')
    return write_event(database, actor=_ACTOR.get() if actor is None else actor,
                       action=action, target=target, details=details)


def audit_connection(conn,actor,action,target,details=None):
    """Explicit-connection bridge for auth/monitor code; never open a global DB."""
    from types import SimpleNamespace
    with audit_context(SimpleNamespace(conn=conn),actor):
        return audit(actor,action,target,details or {})
