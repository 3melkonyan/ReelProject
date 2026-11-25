"""Opt-in settings and automatic learning mail; no configuration/network work at import.

Delivery is reconciled from completed, privacy-passed real publications. A unique
claim prevents concurrent sends; an ambiguous SMTP outcome is never resent.
"""
from __future__ import annotations
from contextlib import suppress

from reel.i18n import t as _t, localized_message, current_language as _language

import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from html import escape
import json
import os
from pathlib import Path
import smtplib
import ssl
from zoneinfo import ZoneInfo

from reel.insights import library_records, video_id
from reel.web import account_mail

LOCAL = ZoneInfo("Asia/Yerevan")
TOPICS = ("new_lessons", "weekly_digest")
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS learning_mail_preferences (user_id INTEGER PRIMARY KEY REFERENCES users(id),"
    " new_lessons INTEGER NOT NULL DEFAULT 1, weekly_digest INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS learning_mail_deliveries (user_id INTEGER NOT NULL REFERENCES users(id),"
    " kind TEXT NOT NULL, event_key TEXT NOT NULL, state TEXT NOT NULL, claimed_at TEXT NOT NULL, sent_at TEXT,"
    " PRIMARY KEY(user_id,kind,event_key))",
    "CREATE TABLE IF NOT EXISTS learning_mail_state (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
)


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _stamp(now):
    return _now(now).astimezone(timezone.utc).isoformat(timespec="microseconds")


def _date(value):
    try:
        return _now(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def ensure_schema(conn):
    # execute preserves transactions owned by callers; executescript would commit them.
    for statement in _SCHEMA:
        conn.execute(statement)


def _eligible(user):
    return bool(user and user["active"] and not user["removed_at"] and not user["deleted_at"])


def preferences(database, user_id):
    ensure_schema(database.conn)
    user = database.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not _eligible(user):
        raise ValueError("inactive account")
    row = database.conn.execute("SELECT * FROM learning_mail_preferences WHERE user_id=?", (user_id,)).fetchone()
    return {"personal": True, **{topic: bool(row[topic]) if row else True for topic in TOPICS}}


def save_preferences(database, user_id, values, *, now=None):
    if not isinstance(values, dict) or not values or set(values) - set(TOPICS) or any(type(v) is not bool for v in values.values()):
        raise ValueError("expected notification booleans")
    preferences(database, user_id)  # Reject inactive or removed accounts first.
    database.conn.execute("INSERT OR IGNORE INTO learning_mail_preferences(user_id,updated_at) VALUES(?,?)", (user_id, _stamp(now)))
    columns = ",".join(topic + "=?" for topic in values)
    database.conn.execute("UPDATE learning_mail_preferences SET " + columns + ",updated_at=? WHERE user_id=?",
                          (*[int(value) for value in values.values()], _stamp(now), user_id))
    return preferences(database, user_id)


def _groups(user):
    try:
        groups = json.loads(user["groups_json"])
        return set(groups) if isinstance(groups, list) and all(isinstance(g, str) for g in groups) else set()
    except (TypeError, ValueError):
        return set()


def _student(user):
    return _eligible(user) and user["role"] == "student" and not dict(user).get("demo_tag")


def _lessons(database):
    # No demo table or legacy sidecar is read. DB publication timestamps are authoritative.
    result = []
    for record in library_records(database):
        job = database.job(record["job_id"])
        publication = database.publication(record["job_id"], "youtube")
        if job["status"] == "done" and not record.get("demo") and publication and _date(publication["created_at"]):
            result.append({**record, "published_at": publication["created_at"]})
    return sorted(result, key=lambda item: (_date(item["published_at"]), item["job_id"]))


def _week(now):
    local = _now(now).astimezone(LOCAL)
    end = local.date() - timedelta(days=local.weekday())
    if local < datetime.combine(end, time(9), LOCAL):
        end -= timedelta(days=7)
    start = end - timedelta(days=7)
    return datetime.combine(start, time(), LOCAL), datetime.combine(end, time(), LOCAL)


@localized_message
def build_message(kind, email, lessons, base_url, name="", *, sender=None, theme="auto", week=None):
    """Pure multipart preview builder; thumbnails are validated remote YouTube images."""
    if kind not in TOPICS or theme not in ("auto", "light", "dark"):
        raise ValueError("unknown learning mail kind/theme")
    recipient, _, _ = account_mail._address(email)
    base = account_mail._url(base_url).rstrip("/")
    clean_name = " ".join(str(name or "").split())[:120]
    greeting = f"{_t('text.account_mail.dear')} {clean_name}," if clean_name else _t('text.account_mail.hello')
    heading = _t('text.student_mail.a_new_lesson_is_available') if kind == "new_lessons" else _t('text.student_mail.this_week_s_lessons')
    period = ""
    if week:
        period = f"{week[0].date().isoformat()} — {(week[1] - timedelta(days=1)).date().isoformat()}"
    intro = _t('text.student_mail.a_new_lesson_was_published_in_your') if kind == "new_lessons" else _t('text.student_mail.here_are_the_lessons_published_in_your')
    safe = []
    for lesson in lessons:
        vid = video_id(lesson.get("url"))
        if not vid or lesson.get("demo") or type(lesson.get("job_id")) is not int:
            continue
        safe.append({"title": str(lesson.get("title") or _t('ui.app.lesson'))[:200], "group": str(lesson.get("group") or "")[:120],
                     "subject": str(lesson.get("subject") or "")[:200], "url": f"{base}/#/library/{lesson['job_id']}",
                     "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"})
    if not safe:
        raise ValueError("no real lessons")
    shown = safe[:10]
    dark = theme == "dark"
    bg, card, text, muted = ("#101214", "#1b1e22", "#f4f5f7", "#b0b6c0") if dark else ("#f4f5f7", "#ffffff", "#17191d", "#626a76")
    button_text = "#17191d" if dark else "#ffffff"
    cards = "".join(f'''<tr><td style="padding:0 28px 22px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td width="136" valign="top" style="padding-right:16px;"><a href="{escape(item['url'], quote=True)}"><img src="{item['thumbnail']}" width="120" alt="{_t('text.student_mail.lesson_thumbnail')}" style="display:block;width:120px;max-width:100%;height:auto;border:0;border-radius:8px;"></a></td><td valign="top"><p class="mail-muted" style="margin:0 0 6px;color:{muted};font-size:12px;line-height:1.6;">{escape(item['group'])} · {escape(item['subject'])}</p><a class="mail-text" href="{escape(item['url'], quote=True)}" style="color:{text};font-size:16px;font-weight:bold;line-height:1.6;text-decoration:none;">{escape(item['title'])}</a><p style="margin:10px 0 0;"><a href="{escape(item['url'], quote=True)}" style="color:{text};font-size:13px;">{_t('ui.record.open_lesson')}</a></p></td></tr></table></td></tr>''' for item in shown)
    prefs = base + "/#/library/settings"
    css = """<style>@media(prefers-color-scheme:dark){.mail-body{background:#101214!important}.mail-card{background:#1b1e22!important}.mail-text{color:#f4f5f7!important}.mail-muted{color:#b0b6c0!important}.mail-button{background:#f4f5f7!important;color:#17191d!important}}</style>""" if theme == "auto" else ""
    html = f'''<!doctype html><html lang="{_language()}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="{('light dark' if theme == 'auto' else theme)}">{css}<title>{heading}</title></head><body class="mail-body" style="margin:0;background:{bg};font-family:Arial,'Noto Sans Armenian',sans-serif;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:28px 12px;"><table role="presentation" width="600" cellpadding="0" cellspacing="0" class="mail-card" style="width:100%;max-width:600px;background:{card};border-radius:16px;"><tr><td bgcolor="#000000" style="padding:20px 28px;background:#000000;border-radius:16px 16px 0 0;"><img src="cid:reel-logo" width="240" height="77" alt="Reel" style="display:block;width:240px;max-width:100%;height:auto;background:#000000;border:0;"></td></tr><tr><td class="mail-text" style="padding:28px;color:{text};"><h1 style="margin:0 0 12px;font-size:26px;line-height:1.4;">{heading}</h1><p class="mail-muted" style="color:{muted};font-size:13px;">{period}</p><p style="font-size:15px;line-height:1.8;">{escape(greeting)}</p><p style="font-size:15px;line-height:1.8;">{intro}</p></td></tr>{cards}<tr><td class="mail-text" style="padding:0 28px 28px;color:{text};"><p style="font-size:14px;line-height:1.8;"><a class="mail-button" href="{escape(base + '/#/library', quote=True)}" style="display:inline-block;padding:14px 22px;border-radius:8px;background:{text};color:{button_text};font-size:14px;font-weight:bold;line-height:1.6;text-decoration:none;">{_t('text.student_mail.open_all_lessons')}{len(safe)})</a></p><p class="mail-muted" style="font-size:12px;line-height:1.8;color:{muted};">{_t('text.student_mail.to_change_email_types_or_unsubscribe_open')} <a class="mail-text" href="{escape(prefs, quote=True)}" style="color:{text};">{_t('text.student_mail.notification_settings')}</a>{_t('text.student_mail.after_signing_in_to_your_account')}</p></td></tr><tr><td style="padding:20px 28px;border-top:1px solid #626a76;"><p class="mail-text" style="margin:0 0 6px;color:{text};font-size:12px;font-weight:bold;">Reel</p><p class="mail-muted" style="margin:0;color:{muted};font-size:11px;line-height:1.7;">{_t('text.account_mail.this_email_was_sent_automatically_please_do')}</p></td></tr></table></td></tr></table></body></html>'''
    message = EmailMessage(policy=SMTP_POLICY)
    message["Subject"] = "Reel․ " + heading + (" · " + period if period else "")
    message["From"] = sender or "Reel <noreply@example.invalid>"
    message["To"] = recipient
    lines = [greeting, heading, period, intro]
    lines += [f"{item['group']} · {item['subject']}\n{item['title']}\n{item['url']}" for item in shown]
    lines += [f"{_t('text.student_mail.all_lessons')} " + base + "/#/library", f"{_t('text.student_mail.notification_settings_sign_in_required')} " + prefs, "Reel"]
    message.set_content("\n\n".join(part for part in lines if part))
    message.add_alternative(html, subtype="html")
    message.get_payload()[-1].add_related(account_mail.LOGO.read_bytes(), maintype="image", subtype="png", cid="<reel-logo>", disposition="inline", filename="reel-email.png")
    if len(message.as_bytes()) >= 100 * 1024:
        raise ValueError("learning message exceeds 100KB")
    return message


def _configuration(root):
    from reel.web import accounts
    base = accounts.public_base(root)
    if not base:
        return None
    cfg = accounts.settings(root)
    if not all(cfg.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")):
        return None
    try:
        port = int(cfg.get("SMTP_PORT") or 587)
        if not 1 <= port <= 65535:
            return None
        account_mail._url(base)
    except (TypeError, ValueError):
        return None
    return base, cfg, port


def _send(message, cfg, port):
    sending = False
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            sending = True
            smtp.send_message(message)
        return "sent"
    except Exception:
        # SMTP may have accepted the message before disconnecting. Never resend that outcome.
        return "uncertain" if sending else "retry"


def run_pending(database, root, now=None, *, max_messages=50):
    """Reconcile publications; callers supply an independent DB connection per thread.

    First configured run establishes a watermark (no historic new-lesson blast).
    Weekly mail is due Monday09:00 Yerevan for the previous complete Monday–Sunday.
    Delivery keys contain only a job id or week start, never a recipient or token.
    """
    now = _now(now)
    config = _configuration(root)
    report = {"sent": 0, "retry": 0, "uncertain": 0, "configured": bool(config)}
    if not config:
        return report
    base, cfg, port = config
    ensure_schema(database.conn)
    stamp = _stamp(now)
    database.conn.execute("INSERT OR IGNORE INTO learning_mail_state(key,value) VALUES('enabled_at',?)", (stamp,))
    enabled = _date(database.conn.execute("SELECT value FROM learning_mail_state WHERE key='enabled_at'").fetchone()[0])
    # An abandoned in-flight claim has an unknown SMTP outcome, so it is retained, not resent.
    database.conn.execute("UPDATE learning_mail_deliveries SET state='uncertain' WHERE state='claimed' AND claimed_at<?", (_stamp(now - timedelta(minutes=10)),))
    lessons = _lessons(database)
    start, end = _week(now)
    attempts = 0
    users = database.conn.execute("SELECT * FROM users WHERE role='student' AND active=1 AND removed_at IS NULL AND deleted_at IS NULL ORDER BY id").fetchall()
    for user in users:
        if not _student(user):
            continue
        scope = _groups(user)
        own = [lesson for lesson in lessons if lesson["group"] in scope and _date(lesson["published_at"]) <= now]
        prefs = preferences(database, user["id"])
        events = []
        if prefs["new_lessons"]:
            events += [("new_lessons", str(lesson["job_id"]), [lesson], None) for lesson in own if _date(lesson["published_at"]) >= enabled]
        if prefs["weekly_digest"]:
            weekly = [lesson for lesson in own if start <= _date(lesson["published_at"]) < end]
            if weekly:
                events.append(("weekly_digest", start.date().isoformat(), weekly, (start, end)))
        for kind, key, selected, week in events:
            if attempts >= max_messages:
                return report
            claimed = database.conn.execute("INSERT OR IGNORE INTO learning_mail_deliveries(user_id,kind,event_key,state,claimed_at) VALUES(?,?,?,'claimed',?)", (user["id"], kind, key, stamp))
            if claimed.rowcount != 1:
                continue
            attempts += 1
            try:
                # Recheck membership, account state and opt-out immediately before delivery.
                fresh = database.conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                current = [lesson for lesson in selected if lesson["group"] in _groups(fresh)] if fresh else []
                if not _student(fresh) or not preferences(database, user["id"])[kind] or not current:
                    database.conn.execute("DELETE FROM learning_mail_deliveries WHERE user_id=? AND kind=? AND event_key=?", (user["id"], kind, key))
                    continue
                from reel.language import user_language
                from reel.web.localization import configured_language
                message = build_message(kind, fresh["email"], current, base, fresh["name"], sender=cfg["SMTP_FROM"], week=week, language=user_language(database.conn, fresh["id"], configured_language(root)))
                state = _send(message, cfg, port)
            except Exception:
                state = "retry"  # No SMTP operation occurred if message construction/validation failed.
            report[state] += 1
            if state == "retry":
                database.conn.execute("DELETE FROM learning_mail_deliveries WHERE user_id=? AND kind=? AND event_key=?", (user["id"], kind, key))
            else:
                database.conn.execute("UPDATE learning_mail_deliveries SET state=?,sent_at=? WHERE user_id=? AND kind=? AND event_key=?", (state, stamp if state == "sent" else None, user["id"], kind, key))
    return report


def install_scheduler(app, env):
    """Register automatic mail lifecycle; tests/deployments may disable via env=0."""
    task = None
    stop = None

    def tick():
        from reel.db import DB
        database = DB(Path(env["ROOT"]) / "reel.db")
        with closing(database.conn):
            return run_pending(database, env["ROOT"])

    async def loop():
        while not stop.is_set():
            with suppress(Exception):
                await asyncio.to_thread(tick)  # No exception text, email addresses, credentials or URLs enter logs.
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=60)

    async def start():
        nonlocal task, stop
        if os.environ.get("REEL_STUDENT_MAIL_ENABLED", "1") != "1":
            return
        stop = asyncio.Event()
        task = asyncio.create_task(loop())

    async def finish():
        if task:
            stop.set()
            await task

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            await start()
            try:
                yield
            finally:
                await finish()

    app.router.lifespan_context = lifespan
