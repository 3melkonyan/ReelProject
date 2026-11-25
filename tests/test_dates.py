"""Only temporary databases, fake metadata/publications and role-scoped HTTP."""

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from test_accounts import invite, register, seed_jobs  # noqa: F401 (autouse: no real DNS/SMTP)
from test_accounts import offline_account_mail as offline_account_mail
from test_web_upload import send

from reel import lesson_dates as dates
from reel.db import DB
from reel.pipeline import Ctx


def test_additive_migration_preserves_existing_jobs_and_is_repeatable(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        'CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,source_path TEXT NOT NULL,sha256 TEXT UNIQUE,grp TEXT,subject TEXT,status TEXT DEFAULT "queued",current_stage TEXT,error TEXT,created_at TEXT,updated_at TEXT)'
    )
    connection.execute(
        "INSERT INTO jobs(source_path,sha256,grp,created_at,updated_at) VALUES(?,?,?,?,?)",
        (
            "original.mp4",
            "untouched",
            "A",
            "2024-01-01T00:00:00+00:00",
            "2024-01-02T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    for _ in range(2):
        database = DB(path)
        job = dict(database.job(1))
        assert (
            job["lesson_date"] is None
            and job["source_path"] == "original.mp4"
            and job["sha256"] == "untouched"
        )
        assert job["created_at"] == "2024-01-01T00:00:00+00:00"
        assert job["updated_at"] == "2024-01-02T00:00:00+00:00"
        database.conn.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            {"format": {"tags": {"com.apple.quicktime.creationdate": "2024-05-03T14:00:00+0400"}}},
            "2024-05-03",
        ),
        ({"streams": [{"tags": {"creation_time": "2024-05-03T22:30:00Z"}}]}, "2024-05-04"),
        (
            {
                "format": {
                    "tags": {
                        "creation_time": "2024-05-03T12:30:00Z",
                        "com.apple.quicktime.creationdate": "2024-05-02T08:00:00+04:00",
                    }
                }
            },
            "2024-05-02",
        ),
        ({"format": {"tags": {"creation_time": "not a date"}}}, None),
        ({"format": {"tags": {"creation_time": "2999-05-03T12:30:00Z"}}}, None),
    ],
)
def test_recording_metadata_and_yerevan_day(raw, expected):
    assert dates.recording_date(raw) == expected


def test_probe_collects_dates_without_second_media_pass(isolated, monkeypatch):
    web, module = isolated
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        return type(
            "Result",
            (),
            {
                "stdout": json.dumps(
                    {
                        "streams": [{"width": 1920, "height": 1080}],
                        "format": {"tags": {"creation_time": "2024-02-03T10:00:00Z"}},
                    }
                )
            },
        )()

    monkeypatch.setattr(module.subprocess, "run", probe)
    info = module._upload_video_info([Path("first.mov"), Path("second.mov")])
    assert len(calls) == 2 and info["recorded_date"] == "2024-02-03"
    assert all("format_tags=creation_time" in c[c.index("-show_entries") + 1] for c in calls)


@pytest.mark.parametrize(
    "manual,recorded,expected",
    [
        (True, "2024-03-02", "2024-03-01"),
        (False, "2024-03-02", "2024-03-02"),
        (False, None, None),
    ],
)
def test_upload_date_precedence_resume_and_job_atomicity(
    isolated, monkeypatch, manual, recorded, expected
):
    web, module = isolated
    monkeypatch.setattr(
        module, "_upload_video_info", lambda parts: {"files": [], "recorded_date": recorded}
    )
    client = web.client("uploader")
    body = {
        "group": "Group-A",
        "lesson_number": 7,
        "lesson_date": "2024-03-01" if manual else dates.today().isoformat(),
        "lesson_date_changed": manual,
        "files": [{"name": "lesson.mov", "size": 12}],
    }
    code, result = client.call("POST", "/api/uploads", body=body)
    assert code == 200, result
    uid = result["id"]
    send(client, uid, 0, b"abcd")
    again = client.call("POST", "/api/uploads", body=body)[1]
    assert again["id"] == uid and again["received"] == [4]
    send(client, uid, 0, b"abcdefghijkl", start=4)
    code, result = client.call("POST", f"/api/uploads/{uid}/finish")
    assert code == 200, result
    database = DB(web.root / "reel.db")
    job = database.job(result["job_id"])
    assert (
        job["lesson_date"] == (expected or dates.today().isoformat()) and job["status"] == "queued"
    )
    assert Path(job["source_path"]).name == "Դաս 7"
    database.conn.close()
    assert client.call("POST", f"/api/uploads/{uid}/finish")[1] == result


def test_resume_auto_date_stays_stable_across_midnight_and_legacy_uid(isolated):
    import hashlib

    web, module = isolated
    client = web.client("uploader")
    body = {"group": "Group-A", "lesson_number": 3, "files": [{"name": "x.mp4", "size": 8}]}
    result = client.call("POST", "/api/uploads", body=body)[1]
    uid = result["id"]
    identity = ["Group-A", "Group-A", "Դաս 3", [{"name": "x.mp4", "size": 8}]]
    assert uid == hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
    meta_path = web.root / "out/uploads" / uid / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["lesson_date"] = "2024-01-01"
    meta_path.write_text(json.dumps(meta))
    body.update(lesson_date=dates.today().isoformat(), lesson_date_changed=False)
    assert client.call("POST", "/api/uploads", body=body)[1]["id"] == uid
    assert json.loads(meta_path.read_text())["lesson_date"] == "2024-01-01"
    body["lesson_date_changed"] = True
    assert client.call("POST", "/api/uploads", body=body)[1]["id"] != uid


@pytest.mark.parametrize("value", ["2999-01-01", "2024-02-30", "20240101", "", None, 12])
def test_invalid_or_future_upload_date_rejected(web, value):
    body = {"group": "Group-A", "lesson_date": value, "files": [{"name": "x.mp4", "size": 1}]}
    assert web.client("uploader").call("POST", "/api/uploads", body=body)[0] == 400


def test_admin_date_editor_authorization_csrf_and_consumers(web):
    ids = seed_jobs(web)
    admin = web.client("admin")
    path = f"/api/jobs/{ids[0]}/date"
    assert web.client("uploader").call("POST", path, body={"lesson_date": "2024-01-02"})[0] == 403
    assert web.client("viewer").call("POST", path, body={"lesson_date": "2024-01-02"})[0] == 403
    assert web.client().call("POST", path, body={"lesson_date": "2024-01-02"})[0] == 401
    assert admin.call("POST", path, body={"lesson_date": "2024-01-02"}, csrf=False)[0] == 400
    assert admin.call("POST", path, body={"lesson_date": "2999-01-01"})[0] == 400
    assert (
        admin.call("POST", path, body={"lesson_date": "2024-01-02"})[1]["lesson_date"]
        == "2024-01-02"
    )
    assert admin.call("GET", f"/api/jobs/{ids[0]}")[1]["lesson_date"] == "2024-01-02"
    assert (
        next(x for x in admin.call("GET", "/api/library")[1] if x["job_id"] == ids[0])[
            "lesson_date"
        ]
        == "2024-01-02"
    )
    assert admin.call("POST", "/api/jobs/999/date", body={"lesson_date": "2024-01-02"})[0] == 404


def test_lecturer_sees_library_and_statistics_only_for_assigned_groups(web):
    ids = seed_jobs(web)
    day = (dates.today() - timedelta(days=2)).isoformat()
    database = DB(web.root / "reel.db")
    for jid in ids:
        database.set_job(jid, lesson_date=day)
    database.conn.close()
    token, _, _ = invite(web, "lecturer", ["Group-A"])
    client, user = register(web, token, "lecturer-dates")
    code, library = client.call("GET", "/api/library")
    assert code == 200
    assert [x["job_id"] for x in library] == [ids[0]] and library[0]["group"] == "Group-A"
    code, stats = client.call("GET", "/api/stats")
    assert code == 200
    assert stats["counts"]["done"] == 1 and stats["publications"]["youtube"] == 1
    assert [x["group"] for x in stats["groups"]] == ["Group-A"]
    assert next(x["count"] for x in stats["daily"] if x["date"] == day) == 1
    assert stats["date_basis"] == "lesson_date"
    assert client.call("GET", f"/api/jobs/{ids[1]}")[0] == 404
    assert client.call("POST", f"/api/jobs/{ids[0]}/date", body={"lesson_date": day})[0] == 403


def test_library_sorts_by_lesson_day_not_publication_time(web):
    ids = seed_jobs(web)
    database = DB(web.root / "reel.db")
    database.set_job(ids[0], lesson_date="2024-03-01")
    database.set_job(ids[1], lesson_date="2024-02-01")
    database.conn.close()
    rows = web.client("viewer").call("GET", "/api/library")[1]
    assert [x["job_id"] for x in rows] == ids


def test_existing_description_date_uses_job_day_without_changing_title_or_topic_dates(tmp_path):
    database = DB(tmp_path / "test.db")
    jid = database.create_job("source", "one", "A", lesson_date="2024-02-03")
    metadata = {
        "title": "Python | Դաս 1 | Անփոփոխ",
        "description": "Դասի ամսաթիվ՝ 2024-01-01\nԹեմայի օրինակում՝ 2020-01-01։",
        "youtube_description": "Date: 01.01.2024\nՄնացածը՝ նույնը։",
    }
    ctx = Ctx(database, jid, Path("source"), tmp_path, "A", {})
    ctx._outputs["metadata"] = metadata
    result = ctx.out("metadata")
    assert result["title"] == metadata["title"]
    assert "2024-02-03" in result["description"] and "2020-01-01" in result["description"]
    assert "03.02.2024" in result["youtube_description"] and metadata["description"].startswith(
        "Դասի ամսաթիվ՝ 2024-01-01"
    )
    assert (
        dates.dated_metadata({"description": "Առանց ամսաթվի նկարագրություն"}, "2024-02-03")[
            "description"
        ]
        == "Առանց ամսաթվի նկարագրություն"
    )
    database.conn.close()
