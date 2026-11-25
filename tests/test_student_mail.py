"""Learning mail tests use temporary databases and fake SMTP/config only."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from reel.db import DB
from reel import student_mail as mail
from reel.web import accounts
from reel.web.student_mail_api import make_router

NOW = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc)  # Monday09:00 Yerevan.
BASE = "https://reel.example.test"
CFG = {"SMTP_HOST": "smtp.test", "SMTP_USER": "test-login", "SMTP_PASSWORD": "not-real", "SMTP_FROM": "Reel <reel@example.test>"}


class FakeSMTP:
    messages = []
    connects = []
    fail_connect = False
    fail_send = False
    on_send = None

    def __init__(self, host, port, timeout):
        self.connects.append((host, port, timeout))
        if self.fail_connect:
            raise OSError("fake connection failure")
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def ehlo(self): pass
    def starttls(self, *, context): assert context is not None
    def login(self, user, password): assert (user, password) == (CFG["SMTP_USER"], CFG["SMTP_PASSWORD"])
    def send_message(self, message):
        if self.fail_send:
            raise OSError("fake ambiguous SMTP disconnect")
        self.messages.append(message)
        if type(self).on_send:
            type(self).on_send()
        return {}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_STUDENT_MAIL_ENABLED", "0")
    FakeSMTP.messages = []; FakeSMTP.connects = []
    FakeSMTP.fail_connect = FakeSMTP.fail_send = False
    FakeSMTP.on_send = None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(accounts, "public_base", lambda root: BASE)
    monkeypatch.setattr(accounts, "settings", lambda root: dict(CFG))
    original = sqlite3.connect
    def connect(path, *args, **kwargs):
        assert Path(path).resolve().is_relative_to(tmp_path), "production DB access"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    # No real env/secrets/public configuration is read, including indirect helper calls.
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args, **kwargs: pytest.fail("dotenv read"))


@pytest.fixture
def db(tmp_path):
    database = DB(tmp_path / "reel.db")
    yield database
    database.conn.close()


def user(db, login="student", groups='["A"]', **fields):
    ident = db.conn.execute("INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at) VALUES(?,?,?,?,?,?,?)",
                           (login, login + "@example.test", "Անի Փորձնական", "!unusable", "student", groups,
                            (NOW - timedelta(days=60)).isoformat())).lastrowid
    for key, value in fields.items():
        db.conn.execute("UPDATE users SET " + key + "=? WHERE id=?", (value, ident))
    return ident


def lesson(db, group="A", published=NOW, *, privacy=True, status="done", dry=False, vid="abc123DEF45"):
    ident = db.create_job("fixture.mp4", "fixture-" + str(db.conn.execute("SELECT count(*) FROM jobs").fetchone()[0]), group, "Python")
    db.set_job(ident, status=status)
    for stage, output in [("privacy", {}), ("metadata", {"title": "Հայերեն դաս"})]:
        db.stage_start(ident, stage)
        if privacy or stage != "privacy":
            db.stage_done(ident, stage, output)
    db.add_publication(ident, "youtube", "dry-fixture" if dry else vid, "https://youtu.be/" + vid)
    db.conn.execute("UPDATE publications SET created_at=? WHERE job_id=?", (published.isoformat(), ident))
    return ident


def initialize(db, root, now=NOW):
    assert mail.run_pending(db, root, now)["sent"] == 0


def test_defaults_partial_update_and_strict_boolean_validation(db):
    ident = user(db)
    assert mail.preferences(db, ident) == {"personal": True, "new_lessons": True, "weekly_digest": True}
    assert mail.save_preferences(db, ident, {"new_lessons": False})["weekly_digest"] is True
    assert mail.save_preferences(db, ident, {"weekly_digest": False}) == {"personal": True, "new_lessons": False, "weekly_digest": False}
    for invalid in ({}, {"new_lessons": 0}, {"new_lessons": "false"}, {"user_id": ident}, [], None):
        with pytest.raises(ValueError): mail.save_preferences(db, ident, invalid)


def test_new_lesson_scope_active_optout_privacy_done_and_no_demo(db, tmp_path):
    wanted = user(db)
    user(db, "foreign", '["B"]')
    user(db, "inactive", active=0)
    user(db, "removed", removed_at=NOW.isoformat())
    user(db, "deleted", deleted_at=NOW.isoformat())
    user(db, "lecturer", role="lecturer")
    opted = user(db, "opted")
    mail.save_preferences(db, opted, {"new_lessons": False})
    if "demo_tag" not in {r[1] for r in db.conn.execute("PRAGMA table_info(users)")}:  # schema has it
        db.conn.execute("ALTER TABLE users ADD COLUMN demo_tag TEXT")
    user(db, "demo", demo_tag="g40-demo")
    initialize(db, tmp_path)
    good = lesson(db, published=NOW + timedelta(seconds=1))
    lesson(db, privacy=False, published=NOW + timedelta(seconds=1))
    lesson(db, status="running", published=NOW + timedelta(seconds=1))
    lesson(db, dry=True, published=NOW + timedelta(seconds=1))
    # A metadata-only demo card has no jobs/publications identity and cannot enter the dispatcher.
    # schema already defines demo_lessons; insert a metadata-only card with its real columns.
    db.conn.execute("INSERT INTO demo_lessons(id,group_name,subject,title,description,lesson_date,demo_tag) "
                    "VALUES('demo-card','AI 2.6','Python','Դեմո դաս','Նկարագրություն','2026-11-01','g40-demo')")
    report = mail.run_pending(db, tmp_path, NOW + timedelta(seconds=2))
    assert report["sent"] == 1 and len(FakeSMTP.messages) == 1
    assert FakeSMTP.messages[0]["To"] == "student@example.test"
    assert f"/#/library/{good}" in FakeSMTP.messages[0].get_body(preferencelist=("plain",)).get_content()
    row = db.conn.execute("SELECT user_id,kind,event_key,state FROM learning_mail_deliveries").fetchone()
    assert tuple(row) == (wanted, "new_lessons", str(good), "sent")
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=3))["sent"] == 0


def test_watermark_avoids_historic_new_lesson_blast(db, tmp_path):
    ident = user(db)
    mail.save_preferences(db, ident, {"weekly_digest": False})
    lesson(db, published=NOW - timedelta(days=1))
    report = mail.run_pending(db, tmp_path, NOW - timedelta(hours=1))
    assert report["sent"] == 0
    lesson(db, published=NOW)
    assert mail.run_pending(db, tmp_path, NOW)["sent"] == 1


def test_weekly_complete_local_week_boundaries_and_dedup(db, tmp_path):
    ident = user(db)
    mail.save_preferences(db, ident, {"new_lessons": False})
    start, end = mail._week(NOW)
    assert start.isoformat() == "2026-11-16T00:00:00+04:00"
    assert end.isoformat() == "2026-11-23T00:00:00+04:00"
    assert mail._week(NOW - timedelta(seconds=1))[1].date().isoformat() == "2026-11-16"
    first = lesson(db, published=start)
    lesson(db, published=end - timedelta(microseconds=1))
    excluded = lesson(db, published=end)
    report = mail.run_pending(db, tmp_path, NOW)
    assert report["sent"] == 1
    message = FakeSMTP.messages[0]
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert "2026-11-16 — 2026-11-22" in plain and f"/#/library/{first}" in plain
    assert f"/#/library/{excluded}\n" not in plain
    assert mail.run_pending(db, tmp_path, NOW + timedelta(days=5))["sent"] == 0
    row = db.conn.execute("SELECT kind,event_key FROM learning_mail_deliveries").fetchone()
    assert tuple(row) == ("weekly_digest", "2026-11-16")


def test_weekly_optout_and_empty_week_send_nothing(db, tmp_path):
    ident = user(db)
    mail.save_preferences(db, ident, {"new_lessons": False, "weekly_digest": False})
    lesson(db, published=NOW - timedelta(days=2))
    assert mail.run_pending(db, tmp_path, NOW)["sent"] == 0
    mail.save_preferences(db, ident, {"weekly_digest": True})
    assert mail.run_pending(db, tmp_path, NOW + timedelta(days=30))["sent"] == 0


@pytest.mark.parametrize("theme", ["light", "dark", "auto"])
def test_multipart_brand_safe_thumbnails_escaping_links_and_wire_limit(theme):
    lessons = [{"job_id": i, "url": "https://youtu.be/abc123DEF45", "title": '<img onerror="bad">' + "ա" * 220,
                "group": "<A>", "subject": "Python", "thumbnail": "http://evil/secret", "demo": False} for i in range(30)]
    message = mail.build_message("weekly_digest", "student@example.test", lessons, BASE, "<Անի>", theme=theme,
                                 week=mail._week(NOW))
    html = message.get_body(preferencelist=("html",)).get_content()
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert "Շաբաթվա դասերը" in html and "2026-11-16 — 2026-11-22" in plain
    assert 'bgcolor="#000000"' in html and 'src="cid:reel-logo"' in html
    assert "&lt;Անի&gt;" in html and "<img onerror" not in html and "http://evil" not in html
    assert "/#/library/settings" in html and "token=" not in html
    assert html.count("https://i.ytimg.com/vi/") == 10
    assert f'class="mail-button" href="{BASE}/#/library" style="display:inline-block;padding:14px 22px;border-radius:8px;background:{"#f4f5f7" if theme == "dark" else "#17191d"};color:{"#17191d" if theme == "dark" else "#ffffff"};' in html
    assert any(part.get("Content-ID") == "<reel-logo>" and part.get_content_type() == "image/png" for part in message.walk())
    assert len(message.as_bytes()) < 100 * 1024


def test_no_public_base_or_smtp_means_no_mail_or_watermark(db, tmp_path, monkeypatch):
    user(db)
    lesson(db)
    monkeypatch.setattr(accounts, "public_base", lambda root: None)
    assert mail.run_pending(db, tmp_path, NOW)["configured"] is False
    assert not FakeSMTP.connects
    assert not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='learning_mail_state'").fetchone()
    monkeypatch.setattr(accounts, "public_base", lambda root: BASE)
    monkeypatch.setattr(accounts, "settings", lambda root: {})
    assert mail.run_pending(db, tmp_path, NOW)["configured"] is False
    assert not FakeSMTP.connects


def test_retry_before_smtp_send_but_ambiguous_and_stale_claims_never_duplicate(db, tmp_path):
    ident = user(db)
    initialize(db, tmp_path)
    first = lesson(db, published=NOW + timedelta(seconds=1))
    FakeSMTP.fail_connect = True
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=2))["retry"] == 1
    FakeSMTP.fail_connect = False
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=3))["sent"] == 1
    second = lesson(db, published=NOW + timedelta(seconds=4))
    FakeSMTP.fail_send = True
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=5))["uncertain"] == 1
    FakeSMTP.fail_send = False
    third = lesson(db, published=NOW + timedelta(seconds=6))
    db.conn.execute("INSERT INTO learning_mail_deliveries VALUES(?,?,?,'claimed',?,NULL)",
                    (ident, "new_lessons", str(third), (NOW - timedelta(hours=1)).isoformat()))
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=7))["sent"] == 0
    states = dict(db.conn.execute("SELECT event_key,state FROM learning_mail_deliveries"))
    assert states == {str(first): "sent", str(second): "uncertain", str(third): "uncertain"}


def test_concurrent_dispatcher_cannot_claim_the_same_event_twice(db, tmp_path):
    user(db)
    initialize(db, tmp_path)
    lesson(db, published=NOW + timedelta(seconds=1))
    entered, release = threading.Event(), threading.Event()
    def wait_for_second():
        entered.set(); assert release.wait(5)
    FakeSMTP.on_send = wait_for_second
    results = []
    def first():
        other = DB(tmp_path / "reel.db")
        with closing(other.conn): results.append(mail.run_pending(other, tmp_path, NOW + timedelta(seconds=2)))
    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    try:
        assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=2))["sent"] == 0
    finally:
        release.set(); thread.join(5)
    assert not thread.is_alive() and len(FakeSMTP.messages) == 1 and results[0]["sent"] == 1


def test_preferences_api_personal_only_csrf_strict_and_own_user(db):
    first, second = user(db), user(db, "other")
    app = FastAPI()
    @app.middleware("http")
    async def auth(request: Request, call_next):
        role = request.headers.get("test-role")
        principal = None if not role else {"id": int(request.headers.get("test-user", first)), "role": role, "legacy": request.headers.get("test-legacy") == "1"}
        token = accounts.PRINCIPAL.set(principal)
        try: return await call_next(request)
        finally: accounts.PRINCIPAL.reset(token)
    def authenticated(request):
        from fastapi import HTTPException
        role = request.headers.get("test-role")
        if not role: raise HTTPException(401, "login required")
        return role
    # DB path captured on this thread: SQLite connections cannot be used by HTTP worker threads.
    path = Path(db.conn.execute("PRAGMA database_list").fetchone()[2])
    app.include_router(make_router({"authenticated": authenticated, "db": lambda: DB(path)}))
    with TestClient(app) as client:
        assert client.get("/api/library/notifications").status_code == 401
        assert client.get("/api/library/notifications", headers={"test-role": "uploader"}).status_code == 403
        headers = {"test-role": "student", "X-Requested-With": "reel"}
        assert client.get("/api/library/notifications", headers=headers).json() == {"user_id": first, "personal": True, "new_lessons": True, "weekly_digest": True}
        assert client.put("/api/library/notifications", headers=headers, json={"new_lessons": False}).json()["new_lessons"] is False
        assert client.put("/api/library/notifications", headers={"test-role": "student"}, json={"new_lessons": True}).status_code == 400
        for bad in ({"user_id": second, "new_lessons": False}, {"weekly_digest": 0}, [], {}):
            assert client.put("/api/library/notifications", headers=headers, json=bad).status_code == 400
        assert client.put("/api/library/notifications", headers=headers, content="[").status_code == 400
        assert client.put("/api/library/notifications", headers=headers, content="x" * 4097).status_code == 413
        before = {uid: mail.preferences(db, uid) for uid in (first, second)}
        for invalid_id in (second, True, str(first), None):
            assert client.put("/api/library/notifications", headers=headers,
                              json={"expected_user_id": invalid_id, "weekly_digest": False}).status_code == 401
        switched = {**headers, "test-user": str(second)}
        assert client.get("/api/library/notifications", headers=switched).json()["user_id"] == second
        assert client.put("/api/library/notifications", headers=switched,
                          json={"expected_user_id": first, "weekly_digest": False}).status_code == 401
        assert {uid: mail.preferences(db, uid) for uid in (first, second)} == before
        saved = client.put("/api/library/notifications", headers=switched,
                           json={"expected_user_id": second, "weekly_digest": False})
        assert saved.status_code == 200 and saved.json() == {"user_id": second, "personal": True, "new_lessons": True, "weekly_digest": False}
        assert mail.preferences(db, first) == before[first]
        legacy = {**headers, "test-legacy": "1"}
        assert client.get("/api/library/notifications", headers=legacy).json() == {"personal": False, "new_lessons": False, "weekly_digest": False}
        assert client.put("/api/library/notifications", headers=legacy, json={"new_lessons": False}).status_code == 403
    assert mail.preferences(db, second)["new_lessons"] is True


def test_scheduler_disabled_and_enabled_with_temp_thread_connection(tmp_path, monkeypatch):
    root = tmp_path
    calls = []
    monkeypatch.setattr(mail, "run_pending", lambda database, folder: calls.append((folder, threading.get_ident())))
    async def exercise(enabled):
        monkeypatch.setenv("REEL_STUDENT_MAIL_ENABLED", enabled)
        app = FastAPI()
        mail.install_scheduler(app, {"ROOT": root})
        async with app.router.lifespan_context(app):
            for _ in range(100):
                if calls or enabled == "0": break
                await asyncio.sleep(.01)
    asyncio.run(exercise("0"))
    assert calls == [] and not (root / "reel.db").exists()
    asyncio.run(exercise("1"))
    assert len(calls) == 1 and calls[0][0] == root and calls[0][1] != threading.get_ident()


@pytest.mark.parametrize("change", ["optout", "membership", "inactive"])
def test_delivery_rechecks_current_optout_membership_and_account_after_claim(db, tmp_path, change):
    user(db)
    initialize(db, tmp_path)
    lesson(db, published=NOW + timedelta(seconds=1))
    updates = {"optout": "INSERT INTO learning_mail_preferences(user_id,new_lessons,weekly_digest,updated_at) VALUES(NEW.user_id,0,1,'fixture') ON CONFLICT(user_id) DO UPDATE SET new_lessons=0;",
               "membership": "UPDATE users SET groups_json='[\"B\"]' WHERE id=NEW.user_id;",
               "inactive": "UPDATE users SET active=0 WHERE id=NEW.user_id;"}
    db.conn.execute("CREATE TRIGGER change_before_delivery AFTER INSERT ON learning_mail_deliveries BEGIN " + updates[change] + " END")
    assert mail.run_pending(db, tmp_path, NOW + timedelta(seconds=2))["sent"] == 0
    assert FakeSMTP.messages == []
    assert not db.conn.execute("SELECT 1 FROM learning_mail_deliveries").fetchone()


def test_dispatch_command_requires_explicit_root_without_opening_database():
    from tools.send_learning_mail import main
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
