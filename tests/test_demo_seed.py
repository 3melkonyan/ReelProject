"""Demo setup/removal only in temporary roots; no production data or services."""
from contextlib import contextmanager
from datetime import date
import builtins
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess

import pytest
import yaml

from reel.db import DB
from reel.demo import read_demo_lessons
from reel.web import accounts, account_mail
from tools import demo_seed as seed

TODAY = date(2026, 11, 20)


@pytest.fixture(autouse=True)
def isolated_only(tmp_path, monkeypatch):
    original_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        assert Path(database).resolve().is_relative_to(tmp_path), "nonfixture database access"
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    protected = [seed.PROJECT / name for name in ("config.yaml", "config.yaml.bak", "reel.db", ".env", "secrets", "work", "archive")]

    def guard(original):
        def checked(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                target = Path(os.path.abspath(os.fsdecode(path)))
                assert not any(target == item or target.is_relative_to(item) for item in protected), "production data access"
            return original(path, *args, **kwargs)
        return checked

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))

    def forbidden(*args, **kwargs):
        raise AssertionError("Demo setup must never contact mail, Slack, YouTube or worker services")

    for module, name in ((accounts, "settings"), (accounts, "public_base"), (accounts, "send_reset"),
                         (account_mail, "send_invitation"), (account_mail, "validate_email"), (subprocess, "run")):
        monkeypatch.setattr(module, name, forbidden)


@contextmanager
def database(root):
    value = DB(root / "reel.db")
    try:
        yield value
    finally:
        value.conn.close()


def real_user(db, login, groups, role="student"):
    return db.conn.execute("INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at) VALUES(?,?,?,?,?,?,?)",
                           (login, login + "@example.com", "Իրական Օգտատեր", "!original", role, json.dumps(groups), "2026-01-01T00:00:00+00:00")).lastrowid


@pytest.fixture
def root(tmp_path):
    config = {"groups": {"Group-A": {"subjects": [{"name": "JavaScript", "playlist": seed.PLAYLIST}],
                                    "slack_channel": seed.SLACK, "lecturers": [], "framing": "auto"},
                          "Real Other": {"subjects": [{"name": "Python", "playlist": "PLkeep"}],
                                         "slack_channel": "CKEEP", "lecturers": []}},
              "operator_slack_channel": "COPERATOR", "cut": {"min_silence_sec": 2, "max_cut_ratio": 0.3}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    (tmp_path / "config.yaml.bak").write_bytes(b"original backup left untouched after removal")
    with database(tmp_path) as db:
        real_user(db, "real-owner", [], "admin")
        real_user(db, "real-member", ["Group-A", "Real Other"])
        job = db.create_job("original.mp4", "original-sha", "Group-A", "JavaScript")
        db.set_job(job, status="done")
        db.conn.execute("INSERT INTO account_settings VALUES('real-setting','keep')")
        from reel.audit import write_event
        write_event(db, action="fixture.real", target="real-record")
    return tmp_path


def config(root):
    return yaml.safe_load((root / "config.yaml").read_text())


def test_demo_snapshot_has_eight_courses_with_durations():
    snapshot = seed.course_snapshot()
    assert snapshot["snapshot_date"] == "2026-11-20" and len(snapshot["courses"]) == 8
    assert sorted(course["duration_months"] for course in snapshot["courses"]) == [4, 4, 4, 7, 7, 10, 15, 15]
    for course in snapshot["courses"]:
        assert "source_url" not in course and "verified_at" not in course
        assert course["published_at"] is None and course["subject_schedule_is_illustrative"] is True
        assert len(course["subject_labels"]) <= 5


def test_apply_has_correct_courses_rosters_periods_flags_and_private_credentials(root, capsys):
    report = seed.apply(root, today=TODAY)
    assert report["status"] == "applied" and report["groups"] == 19
    data = config(root)
    assert "Group-A" not in data["groups"] and "AI 2.6" in data["groups"]
    assert data["groups"]["AI 2.6"]["subjects"][0] == {"name": "JavaScript", "playlist": seed.PLAYLIST}
    assert len(data["groups"]["AI 2.6"]["subjects"]) == 5
    assert data["groups"]["AI 2.6"]["slack_channel"] == seed.SLACK
    credentials = root / seed.CREDENTIALS
    assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
    secret = json.loads(credentials.read_text())
    assert secret["demo_tag"] == seed.TAG and len(secret["accounts"]) == 3
    assert secret["password"] not in capsys.readouterr().out
    assert secret["password"] not in (root / "config.yaml").read_text()
    with database(root) as db:
        users = db.conn.execute("SELECT * FROM users WHERE demo_tag=?", (seed.TAG,)).fetchall()
        usable = [user for user in users if accounts.verify_password(secret["password"], user["password_hash"])]
        assert {user["login"] for user in usable} == {"demo-student", "demo-ta", "demo-lecturer"}
        assert len({user["password_hash"] for user in usable}) == 3
        assert all(user["email"].endswith("@example.com") for user in users)
        assert all(any("Ա" <= c <= "Ֆ" or "ա" <= c <= "ֆ" for c in user["name"]) for user in users)
        assert secret["password"] not in " ".join(row[0] for row in db.conn.execute("SELECT payload_json FROM demo_registry"))
        assert db.conn.execute("SELECT grp,subject FROM jobs WHERE sha256='original-sha'").fetchone()[:] == ("AI 2.6", "JavaScript")
        assert db.conn.execute("SELECT demo_tag FROM users WHERE login='real-member'").fetchone()[0] is None
        from reel.web.group_management import group_snapshot
        snapshots = [group_snapshot(db, name, group, TODAY) for name, group in data["groups"].items() if name != "Real Other"]
        assert all(15 <= item["student_count"] <= 20 for item in snapshots)
        assert all(1 <= len(item["team"]["tas"]) <= 2 for item in snapshots)
        assert all(item["team"]["lecturers"] for item in snapshots)
        assert any(item["completed"] for item in snapshots)
        assert any(item["end_soon"] for item in snapshots)
        assert db.conn.execute("SELECT count(*) FROM users WHERE demo_tag IS NULL").fetchone()[0] == 2
        assert db.conn.execute("SELECT count(*) FROM audit_events WHERE demo=0").fetchone()[0] == 1


def test_apply_is_idempotent(root):
    seed.apply(root, today=TODAY)
    before = ((root / "config.yaml").read_bytes(), (root / seed.CREDENTIALS).read_bytes())
    with database(root) as db:
        counts = [db.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("users", "demo_registry", "demo_lessons", "audit_events")]
    assert seed.apply(root, today=TODAY)["status"] == "already_applied"
    assert ((root / "config.yaml").read_bytes(), (root / seed.CREDENTIALS).read_bytes()) == before
    with database(root) as db:
        assert counts == [db.conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("users", "demo_registry", "demo_lessons", "audit_events")]


def test_remove_restores_original_real_state_and_backup(root):
    before = copy.deepcopy(config(root))
    backup = (root / "config.yaml.bak").read_bytes()
    with database(root) as db:
        original_user = dict(db.conn.execute("SELECT * FROM users WHERE login='real-member'").fetchone())
        original_job = dict(db.conn.execute("SELECT * FROM jobs WHERE sha256='original-sha'").fetchone())
    seed.apply(root, today=TODAY)
    result = seed.remove(root)
    assert result == {"status": "removed", "demo_tag": seed.TAG, "conflicts": []}
    assert config(root) == before and (root / "config.yaml.bak").read_bytes() == backup
    assert not (root / seed.CREDENTIALS).exists()
    with database(root) as db:
        assert db.conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        assert db.conn.execute("SELECT count(*) FROM demo_registry").fetchone()[0] == 0
        assert db.conn.execute("SELECT count(*) FROM demo_lessons").fetchone()[0] == 0
        assert db.conn.execute("SELECT count(*) FROM group_metadata").fetchone()[0] == 0
        assert db.conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        assert dict(db.conn.execute("SELECT * FROM jobs WHERE sha256='original-sha'").fetchone()) == original_job
        user = dict(db.conn.execute("SELECT * FROM users WHERE login='real-member'").fetchone())
        assert json.loads(user["groups_json"]) == json.loads(original_user["groups_json"])
        for field in ("name", "email", "login", "password_hash", "role", "active", "created_at", "demo_tag"):
            assert user[field] == original_user[field]
        assert user["session_version"] > original_user["session_version"]
        assert db.conn.execute("SELECT value FROM account_settings WHERE key='real-setting'").fetchone()[0] == "keep"
    assert seed.remove(root)["status"] == "not_applied"


def test_metadata_reader_is_scoped_and_never_exposes_playable_links_or_passwords(root):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        all_rows = read_demo_lessons(db)
        scoped = read_demo_lessons(db, ["AI 2.6"])
        assert all_rows and scoped and len(scoped) < len(all_rows)
        assert read_demo_lessons(db, []) == []
        assert all(row["group"] == "AI 2.6" for row in scoped)
        assert all(row["demo"] and not row["playable"] and not row["has_video"] and row["url"] is None for row in all_rows)
        assert all(row["thumbnail"].startswith("/brand/") and row["lecturer"]["name"] for row in all_rows)
        assert not {"password", "password_hash", "email", "source_path"}.intersection(all_rows[0])
        assert seed.read_demo_lessons(db, ["AI 2.6"]) == scoped


@pytest.mark.parametrize("kind", ["group", "account", "credentials", "active-job"])
def test_apply_refuses_non_demo_conflicts_without_touching_state(root, kind):
    if kind == "group":
        data = config(root); data["groups"]["AI Application Development 2.6"] = copy.deepcopy(data["groups"]["Real Other"])
        (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    if kind == "account":
        with database(root) as db:
            real_user(db, "demo-student", ["Real Other"])
    if kind == "credentials":
        (root / seed.CREDENTIALS).parent.mkdir(parents=True)
        (root / seed.CREDENTIALS).write_text("unrelated file")
    if kind == "active-job":
        with database(root) as db:
            db.conn.execute("UPDATE jobs SET status='running' WHERE sha256='original-sha'")
    before = (root / "config.yaml").read_bytes()
    with database(root) as db:
        count = db.conn.execute("SELECT count(*) FROM users").fetchone()[0]
    with pytest.raises(seed.DemoConflict):
        seed.apply(root, today=TODAY)
    assert (root / "config.yaml").read_bytes() == before
    with database(root) as db:
        assert db.conn.execute("SELECT count(*) FROM users").fetchone()[0] == count
        assert db.conn.execute("SELECT count(*) FROM demo_registry").fetchone()[0] == 0


def test_remove_keeps_unrelated_config_and_new_memberships(root):
    seed.apply(root, today=TODAY)
    data = config(root); data["operator_slack_channel"] = "CNEWREAL"
    data["groups"]["New Real"] = copy.deepcopy(data["groups"]["Real Other"])
    (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    with database(root) as db:
        row = db.conn.execute("SELECT id,groups_json FROM users WHERE login='real-member'").fetchone()
        db.conn.execute("UPDATE users SET groups_json=? WHERE id=?", (json.dumps(json.loads(row["groups_json"]) + ["New Real"]), row["id"]))
    assert seed.remove(root)["status"] == "removed"
    data = config(root)
    assert data["operator_slack_channel"] == "CNEWREAL" and "New Real" in data["groups"]
    with database(root) as db:
        groups = json.loads(db.conn.execute("SELECT groups_json FROM users WHERE login='real-member'").fetchone()[0])
        assert groups == ["Group-A", "Real Other", "New Real"]


@pytest.mark.parametrize("change", ["configuration", "new-real-member", "new-real-job", "running-job"])
def test_changed_real_replacement_is_retained_on_remove(root, change):
    seed.apply(root, today=TODAY)
    if change == "configuration":
        data = config(root); data["groups"]["AI 2.6"]["slack_channel"] = "CCHANGED"
        (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    with database(root) as db:
        if change == "new-real-member":
            real_user(db, "new-real", ["AI 2.6"])
        if change == "new-real-job":
            ident = db.create_job("new.mp4", "new-real-sha", "AI 2.6", "JavaScript")
            db.set_job(ident, status="done")
        if change == "running-job":
            db.conn.execute("UPDATE jobs SET status='running' WHERE sha256='original-sha'")
    result = seed.remove(root)
    assert result["status"] == "removed_with_conflicts" and result["conflicts"]
    assert "AI 2.6" in config(root)["groups"] and "Group-A" not in config(root)["groups"]
    with database(root) as db:
        assert db.conn.execute("SELECT grp FROM jobs WHERE sha256='original-sha'").fetchone()[0] == "AI 2.6"
        assert "AI 2.6" in json.loads(db.conn.execute("SELECT groups_json FROM users WHERE login='real-member'").fetchone()[0])


@pytest.mark.parametrize("change", ["real-member", "real-job", "group-edit"])
def test_adopted_demo_group_is_not_deleted(root, change):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        if change == "real-member":
            real_user(db, "new-real", ["AI Application Development 2.6"])
        if change == "real-job":
            ident = db.create_job("new.mp4", "new-real-sha", "AI Application Development 2.6", "C և Linux")
            db.set_job(ident, status="done")
    if change == "group-edit":
        data = config(root); data["groups"]["AI Application Development 2.6"]["slack_channel"] = "CREAL"
        (root / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    result = seed.remove(root)
    assert result["status"] == "removed_with_conflicts"
    assert "AI Application Development 2.6" in config(root)["groups"]
    with database(root) as db:
        assert db.conn.execute("SELECT 1 FROM group_metadata WHERE name='AI Application Development 2.6'").fetchone()


def test_setup_failure_rolls_back_rows_config_and_credentials(root, monkeypatch):
    before = (root / "config.yaml").read_bytes()
    original = seed._atomic

    def fail(path, *args, **kwargs):
        if path == root / seed.CREDENTIALS:
            raise OSError("fixture disk error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(seed, "_atomic", fail)
    with pytest.raises(OSError):
        seed.apply(root, today=TODAY)
    assert (root / "config.yaml").read_bytes() == before
    assert not (root / seed.CREDENTIALS).exists()
    with database(root) as db:
        assert db.conn.execute("SELECT count(*) FROM demo_registry").fetchone()[0] == 0
        assert db.conn.execute("SELECT count(*) FROM users").fetchone()[0] == 2


def test_credentials_created_by_someone_else_mid_apply_are_not_overwritten(root, monkeypatch):
    original = seed._save_config
    before = (root / "config.yaml").read_bytes()

    def concurrent(root, config):
        original(root, config)
        (root / seed.CREDENTIALS).parent.mkdir(parents=True, exist_ok=True)
        (root / seed.CREDENTIALS).write_text("unrelated new file")

    monkeypatch.setattr(seed, "_save_config", concurrent)
    with pytest.raises(FileExistsError):
        seed.apply(root, today=TODAY)
    assert (root / seed.CREDENTIALS).read_text() == "unrelated new file"
    assert (root / "config.yaml").read_bytes() == before


def test_changed_private_credential_file_is_not_removed(root):
    seed.apply(root, today=TODAY)
    (root / seed.CREDENTIALS).write_text("operator modified this file")
    result = seed.remove(root)
    assert result["status"] == "removed_with_conflicts"
    assert (root / seed.CREDENTIALS).read_text() == "operator modified this file"


def test_missing_cli_mode_does_not_touch_default_root():
    with pytest.raises(SystemExit) as error:
        seed.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("field,value", [("role", "lecturer"), ("email", "adopted@example.com"),
                                         ("password_hash", "!adopted-password"), ("groups_json", '["Real Other"]')])
def test_changed_demo_accounts_are_preserved_even_if_the_tag_is_unchanged(root, field, value):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        ident = db.conn.execute("SELECT id FROM users WHERE login='demo-student'").fetchone()[0]
        db.conn.execute("UPDATE users SET " + field + "=? WHERE id=?", (value, ident))
    result = seed.remove(root)
    assert result["status"] == "removed_with_conflicts"
    with database(root) as db:
        row = db.conn.execute("SELECT * FROM users WHERE id=?", (ident,)).fetchone()
        assert row is not None and row["demo_tag"] == seed.TAG and row[field] == value
        assert all(group in config(root)["groups"] for group in json.loads(row["groups_json"]))
        assert db.conn.execute("SELECT 1 FROM demo_registry WHERE kind='user' AND key=?", (str(ident),)).fetchone()


def test_last_login_and_group_json_formatting_do_not_prevent_demo_cleanup(root):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        row = db.conn.execute("SELECT id,groups_json FROM users WHERE login='demo-lecturer'").fetchone()
        db.conn.execute("UPDATE users SET last_login_at=?,groups_json=? WHERE id=?",
                        ("2026-11-20T23:00:00+00:00", json.dumps(list(reversed(json.loads(row["groups_json"]))), indent=2), row["id"]))
    assert seed.remove(root)["status"] == "removed"
    with database(root) as db:
        assert not db.conn.execute("SELECT 1 FROM users WHERE demo_tag=?", (seed.TAG,)).fetchone()


def test_failed_demo_user_deletion_rolls_back_its_token_deletion(root):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        ident = db.conn.execute("SELECT id FROM users WHERE login='demo-student'").fetchone()[0]
        db.conn.execute("CREATE TABLE real_reference(user_id INTEGER REFERENCES users(id))")
        db.conn.execute("INSERT INTO real_reference VALUES(?)", (ident,))
        token = db.conn.execute("INSERT INTO account_tokens(digest,kind,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
                                ("fixture-token-digest", "reset", ident, 0, 9999999999)).lastrowid
        before = dict(db.conn.execute("SELECT * FROM account_tokens WHERE id=?", (token,)).fetchone())
    assert seed.remove(root)["status"] == "removed_with_conflicts"
    with database(root) as db:
        assert db.conn.execute("SELECT 1 FROM users WHERE id=?", (ident,)).fetchone()
        assert dict(db.conn.execute("SELECT * FROM account_tokens WHERE id=?", (token,)).fetchone()) == before
        assert db.conn.execute("SELECT user_id FROM real_reference").fetchone()[0] == ident


def test_other_owner_tagged_group_a_members_are_migrated_and_restored_as_nonowned(root):
    with database(root) as db:
        db.conn.execute("UPDATE users SET demo_tag='another-owner' WHERE login='real-member'")
    seed.apply(root, today=TODAY)
    with database(root) as db:
        row = db.conn.execute("SELECT groups_json,demo_tag FROM users WHERE login='real-member'").fetchone()
        assert json.loads(row["groups_json"]) == ["AI 2.6", "Real Other"] and row["demo_tag"] == "another-owner"
    assert seed.remove(root)["status"] == "removed"
    with database(root) as db:
        row = db.conn.execute("SELECT groups_json,demo_tag FROM users WHERE login='real-member'").fetchone()
        assert json.loads(row["groups_json"]) == ["Group-A", "Real Other"] and row["demo_tag"] == "another-owner"


@pytest.mark.parametrize("group", ["AI Application Development 2.6", "AI 2.6"])
def test_other_owner_tagged_new_members_protect_their_group_during_removal(root, group):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        ident = real_user(db, "another-demo-owner", [group])
        db.conn.execute("UPDATE users SET demo_tag='another-owner' WHERE id=?", (ident,))
    assert seed.remove(root)["status"] == "removed_with_conflicts"
    assert group in config(root)["groups"]
    with database(root) as db:
        row = db.conn.execute("SELECT groups_json,demo_tag FROM users WHERE id=?", (ident,)).fetchone()
        assert json.loads(row["groups_json"]) == [group] and row["demo_tag"] == "another-owner"


def test_fixed_cohort_cadence_and_full_course_group_names_are_stable():
    from reel.config import GROUP_NAME
    cohorts = seed._cohorts(TODAY)
    assert len(cohorts) == 19
    for course in seed.course_snapshot()["courses"]:
        own = [item for item in cohorts if item["course"]["id"] == course["id"]]
        expected = {date(2026, 10, 1), date(2026, 6, 1)}
        if course["id"] in ("ai-modern", "ai-junior", "ai-uxui"):
            expected.add(date(2026, 2, 1))
        assert {item["start"] for item in own} == expected
        for item in own:
            assert item["end"] == seed._months(item["start"], course["duration_months"]) - seed.timedelta(days=1)
            assert GROUP_NAME.fullmatch(item["name"]) and len(item["name"]) <= 60
            if item["name"] != "AI 2.6":
                assert item["name"].startswith(course["group_prefix"] + " ")
            assert course["source_name"] == course["name"]
    assert seed._cohorts(date(2030, 1, 1)) == cohorts
    assert any(item["name"] == "AI UX UI 2.4" for item in cohorts)
    assert any(item["name"] == "Ծրագրավորում զինակոչիկների համար 1991 2.6" for item in cohorts)
    modern = next(item for item in cohorts if item["name"] == "AI Modern Software Developer 2.4")
    assert modern["end"] == date(2026, 11, 30)


@pytest.mark.parametrize("login", ["demo-student", "demo-g40-lecturer-ai-appdev"])
def test_adopted_demo_user_preserves_every_current_group_and_its_metadata_team(root, login):
    seed.apply(root, today=TODAY)
    with database(root) as db:
        user = db.conn.execute("SELECT id,groups_json FROM users WHERE login=?", (login,)).fetchone()
        memberships = json.loads(user["groups_json"])
        groups_before = {name: copy.deepcopy(config(root)["groups"][name]) for name in memberships}
        metadata_before = {name: dict(db.conn.execute("SELECT * FROM group_metadata WHERE name=?", (name,)).fetchone())
                           for name in memberships}
        db.conn.execute("UPDATE users SET password_hash='!adopted-account' WHERE id=?", (user["id"],))
    assert seed.remove(root)["status"] == "removed_with_conflicts"
    with database(root) as db:
        retained = db.conn.execute("SELECT groups_json,password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        assert json.loads(retained["groups_json"]) == memberships and retained["password_hash"] == "!adopted-account"
        for name in memberships:
            assert config(root)["groups"][name] == groups_before[name]
            metadata = dict(db.conn.execute("SELECT * FROM group_metadata WHERE name=?", (name,)).fetchone())
            assert metadata == metadata_before[name]
            team = {item["lecturer_id"] for item in json.loads(metadata["subjects_json"])}
            team.update(json.loads(metadata["ta_ids_json"]))
            assert all(db.conn.execute("SELECT 1 FROM users WHERE id=?", (ident,)).fetchone() for ident in team)
