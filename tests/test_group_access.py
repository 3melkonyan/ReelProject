"""Completed groups retain library access and deny new uploads, on temporary roots."""

import pytest

from reel.db import DB
from reel.web import accounts as a
from reel.web.group_status import completed_groups

PASSWORD = "fixture-group-password"


def member(web, role, group="Group-A"):
    database = DB(web.root / "reel.db")
    a.create_user(
        database,
        role + "-member",
        PASSWORD,
        {
            "name": "Փորձնական հաշիվ",
            "email": role + "@example.test",
            "role": role,
            "groups": [group] if role != "admin" else [],
        },
    )
    database.conn.close()
    client = web.client()
    assert (
        client.call("POST", "/api/login", body={"login": role + "-member", "password": PASSWORD})[0]
        == 200
    )
    return client


def test_group_completion_roundtrip_does_not_change_config_or_membership(web):
    admin = web.client("admin")
    lecturer = member(web, "lecturer")
    student = member(web, "student")
    database = DB(web.root / "reel.db")
    jid = database.create_job("retained.mp4", "retained-lesson", "Group-A")
    database.set_job(jid, status="done")
    database.stage_start(jid, "metadata")
    database.stage_done(jid, "metadata", {"title": "Պահպանվող դաս", "chapters": []})
    database.stage_start(jid, "privacy")
    database.stage_done(jid, "privacy", {})
    database.add_publication(jid, "youtube", "abcdefghijk", "https://youtu.be/abcdefghijk")
    before_users = [tuple(row) for row in database.conn.execute("SELECT * FROM users")]
    config = (web.root / "config.yaml").read_bytes()
    assert admin.call("POST", "/api/admin/group-status/Group-A", body={"completed": True}) == (
        200,
        {"name": "Group-A", "completed": True},
    )
    assert completed_groups(database) == {"Group-A"}
    assert (web.root / "config.yaml").read_bytes() == config
    assert [tuple(row) for row in database.conn.execute("SELECT * FROM users")] == before_users
    assert admin.call("GET", "/api/admin/group-status")[1]["groups"][0] == {
        "name": "Group-A",
        "completed": True,
    }
    assert [g["name"] for g in admin.call("GET", "/api/groups")[1]["groups"]] == ["Group-B"]
    assert lecturer.call("GET", "/api/groups")[1]["groups"] == []
    assert student.call("GET", "/api/library")[0] == 200
    assert "Պահպանվող դաս" in str(student.call("GET", "/api/library")[1])
    assert student.call("GET", "/api/accounts/profile")[1]["active"] == 1
    upload = {"group": "Group-A", "lesson": "New", "files": [{"name": "a.mp4", "size": 3}]}
    assert lecturer.call("POST", "/api/uploads", body=upload)[0] == 409
    assert lecturer.call("GET", "/api/uploads/next-number?group=Group-A")[0] == 409
    assert (
        admin.call("POST", "/api/admin/group-status/Group-A", body={"completed": False})[0] == 200
    )
    assert not completed_groups(database)
    assert lecturer.call("POST", "/api/uploads", body=upload)[0] == 200
    database.conn.close()


@pytest.mark.parametrize("who", [None, "viewer", "uploader", "student", "lecturer"])
def test_completion_endpoint_role_matrix(web, who):
    client = member(web, who) if who in ("student", "lecturer") else web.client(who)
    expected = 401 if who is None else 403
    assert client.call("GET", "/api/admin/group-status")[0] == expected
    assert (
        client.call("POST", "/api/admin/group-status/Group-A", body={"completed": True})[0]
        == expected
    )
    database = DB(web.root / "reel.db")
    assert not completed_groups(database)
    database.conn.close()


def test_completion_requires_csrf_and_boolean(web):
    client = web.client("admin")
    assert (
        client.call(
            "POST", "/api/admin/group-status/Group-A", body={"completed": True}, csrf=False
        )[0]
        == 400
    )
    for data in (
        {"completed": 1},
        {"completed": "true"},
        {},
        [],
        {"completed": True, "extra": True},
    ):
        assert client.call("POST", "/api/admin/group-status/Group-A", body=data)[0] == 400
    assert (
        client.call("POST", "/api/admin/group-status/Unknown", body={"completed": True})[0] == 404
    )
