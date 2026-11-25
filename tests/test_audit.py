"""Audit: temp SQLite/HTTP only, with fake mail and publication dependencies."""

import json
import sqlite3
from contextlib import closing
from urllib.parse import urlencode

import pytest

from reel.audit import audit, audit_context, write_event
from reel.db import DB
from reel.web import accounts


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from reel.web import account_mail

    monkeypatch.setattr(account_mail, "validate_email", lambda value: [])
    monkeypatch.setattr(account_mail, "send_invitation", lambda *args: False)
    monkeypatch.setattr(account_mail, "send_reset", lambda *args: False)


def test_explicit_context_and_redaction(tmp_path):
    with pytest.raises(RuntimeError):
        audit(1, "user.role", "user:2", {})
    db = DB(tmp_path / "test.db")
    with closing(db.conn):
        with audit_context(db, actor=1):
            ident = audit(
                None,
                "user.role",
                "user:2",
                {
                    "before": "student",
                    "after": "lecturer",
                    "password": "never-store-this",
                    "email": "private@example.com",
                    "reason": "https://example.com/#/invite/secret",
                    "groups": ["Group-A", "xoxb-never-store-this"],
                    "subjects": [{"name": "Python", "token": "private-token"}],
                },
            )
        row = db.conn.execute("SELECT * FROM audit_events WHERE id=?", (ident,)).fetchone()
        assert row["actor"] == 1
        data = json.loads(row["details"])
        assert data["before"] == "student" and data["after"] == "lecturer"
        assert data["reason"] == "[REDACTED]" and data["groups"][1] == "[REDACTED]"
        for secret in ("never-store-this", "private@example.com", "private-token", "https://"):
            assert secret not in row["details"]
        assert data["subjects"] == [{"name": "Python"}]
        with pytest.raises(ValueError):
            write_event(db, action="bad action", target="job:1")


def test_publication_and_audit_are_atomic_and_not_duplicated(tmp_path, monkeypatch):
    db = DB(tmp_path / "test.db")
    with closing(db.conn):
        job = db.create_job("local.mp4", "local-sha", "Group-A")
        db.add_publication(job, "youtube", "not-a-real-id", "https://example.test/private")
        assert db.conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        row = db.conn.execute("SELECT * FROM audit_events").fetchone()
        assert row["target"] == f"job:{job}" and json.loads(row["details"]) == {"target": "youtube"}
        assert "example.test" not in row["details"]
        with pytest.raises(sqlite3.IntegrityError):
            db.add_publication(job, "youtube", "duplicate", "https://example.test/duplicate")
        assert db.conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        import reel.audit as module

        def fail(*args, **kwargs):
            raise RuntimeError("Audit storage unavailable")

        monkeypatch.setattr(module, "write_event", fail)
        with pytest.raises(RuntimeError):
            db.add_publication(job, "slack", "fake", None)
        assert db.publication(job, "slack") is None


def test_audit_context_participates_in_callers_transaction(tmp_path):
    db = DB(tmp_path / "test.db")
    with closing(db.conn):
        db.conn.execute("BEGIN IMMEDIATE")
        with audit_context(db):
            audit(None, "group.team", "group:Group-A", {"ta_ids": [1, 2]})
        db.conn.execute("ROLLBACK")
        assert db.conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0


@pytest.mark.parametrize("role", [None, "viewer", "uploader"])
def test_audit_is_admin_only(web, role):
    client = web.client(role)
    assert client.call("GET", "/api/admin/audit")[0] == (401 if role is None else 403)


def test_invites_user_changes_and_retry_are_logged_without_credentials(web, monkeypatch):
    admin = web.client("admin")
    db = DB(web.root / "reel.db")
    with closing(db.conn):
        uid = accounts.create_user(
            db,
            "audit-member",
            "password-for-audit-fixture",
            {
                "name": "Փորձնական անձ",
                "email": "audit@example.test",
                "role": "student",
                "groups": ["Group-A"],
            },
        )
    assert (
        admin.call(
            "POST", f"/api/accounts/users/{uid}", body={"role": "lecturer", "groups": ["Group-B"]}
        )[0]
        == 200
    )
    status, invitation = admin.call(
        "POST",
        "/api/accounts/invitations",
        body={
            "name": "Նոր մարդ",
            "email": "new@example.test",
            "role": "student",
            "groups": ["Group-A"],
        },
    )
    assert status == 200
    assert admin.call("POST", f"/api/accounts/users/{uid}/remove")[0] == 200
    assert admin.call("POST", f"/api/accounts/users/{uid}/restore")[0] == 200
    assert admin.call("POST", f"/api/accounts/users/{uid}/remove")[0] == 200
    assert admin.call("POST", f"/api/accounts/users/{uid}/delete")[0] == 200
    from reel import pipeline

    monkeypatch.setattr(pipeline, "retry", lambda db, jid: db.set_job(jid, status="queued"))
    db = DB(web.root / "reel.db")
    with closing(db.conn):
        job = db.create_job("test.mp4", "audit-job", "Group-A")
        db.set_job(job, status="stopped")
    assert admin.call("POST", f"/api/jobs/{job}/retry")[0] == 200
    code, data = admin.call("GET", "/api/admin/audit")
    assert code == 200
    actions = [row["action"] for row in data["items"]]
    assert {
        "user.role",
        "user.groups",
        "invite.create",
        "user.remove",
        "user.restore",
        "user.delete",
        "job.retry",
    } <= set(actions)
    assert all(row["actor"] == 1 for row in data["items"])
    encoded = json.dumps(data)
    assert "audit@example.test" not in encoded and "new@example.test" not in encoded
    assert invitation["link"].rsplit("/", 1)[1] not in encoded
    assert "password-for-audit-fixture" not in encoded
    filtered = admin.call("GET", "/api/admin/audit?action=user.remove&actor=1&page_size=1&page=2")[
        1
    ]
    assert filtered["total"] == 2 and len(filtered["items"]) == 1
    before = data["total"]
    assert admin.call("POST", "/api/accounts/users/999/remove")[0] == 404
    assert admin.call("POST", f"/api/jobs/{job}/retry", csrf=False)[0] == 400
    assert admin.call("GET", "/api/admin/audit")[1]["total"] == before


def test_audit_date_filters_use_local_days_and_validate_input(web):
    admin = web.client("admin")
    db = DB(web.root / "reel.db")
    with closing(db.conn):
        write_event(db, actor=1, action="group.complete", target="group:Group-A")
        db.conn.execute("UPDATE audit_events SET created_at='2026-11-19T21:00:00+00:00'")
    params = urlencode({"from": "2026-11-20", "to": "2026-11-20"})
    assert admin.call("GET", "/api/admin/audit?" + params)[1]["total"] == 1
    assert admin.call("GET", "/api/admin/audit?to=2026-11-19")[1]["total"] == 0
    for query in (
        "page=0",
        "page_size=101",
        "actor=-1",
        "action=not%20valid",
        "from=2026-11-22&to=2026-11-20",
        "from=not-a-date",
    ):
        assert admin.call("GET", "/api/admin/audit?" + query)[0] == 400


def test_database_retry_hook_covers_cli_without_duplicate_events(tmp_path):
    db = DB(tmp_path / "test.db")
    with closing(db.conn):
        job = db.create_job("local.mp4", "retry-sha", "Group-A")
        db.set_job(job, status="queued")
        assert (
            db.conn.execute(
                "SELECT count(*) FROM audit_events WHERE action='job.retry'"
            ).fetchone()[0]
            == 0
        )
        db.set_job(job, status="stopped")
        from reel.pipeline import retry

        with audit_context(db, actor=2):
            retry(db, job)
        retry(db, job)
        rows = db.conn.execute("SELECT * FROM audit_events WHERE action='job.retry'").fetchall()
        assert len(rows) == 1 and rows[0]["actor"] == 2 and rows[0]["target"] == f"job:{job}"
