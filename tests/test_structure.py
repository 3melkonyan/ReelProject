from contextlib import closing

import pytest

from reel.db import DB
from reel.web.accounts import create_user
from reel.web.courses import catalog, course_id, short_group


def test_catalog_and_short_names(hy_locale):
    assert len(catalog()) == 8
    assert course_id("AI 2.6") == "ai-modern"
    assert short_group("Ծրագրավորում զինակոչիկների համար ԱՏՄԿ 2.5") == "ԱՏՄԿ 2.5"


def test_course_details_scope_and_ta_search_are_admin_only(web):
    with closing(DB(web.root / "reel.db").conn) as conn:
        from types import SimpleNamespace

        d = SimpleNamespace(conn=conn)
        for login, role, group in [
            ("lect-one", "lecturer", "Group-A"),
            ("student-one", "student", "Group-A"),
            ("other-one", "student", "Group-B"),
        ]:
            create_user(
                d,
                login,
                "strong-test-password",
                dict(name=login, email=login + "@example.test", role=role, groups=[group]),
            )
    c = web.client("admin")
    status, data = c.call("GET", "/api/courses")
    assert status == 200 and len(data["courses"]) == 8
    assert c.call("GET", "/api/group-view/Group-A")[1]["can_students"]
    assert c.call("GET", "/api/admin/group-students/Group-A?q=s")[1]["items"] == []
    found = c.call("GET", "/api/admin/group-students/Group-A?q=student")[1]["items"]
    assert len(found) == 1 and "email" not in found[0]
    student = web.client()
    student.call(
        "POST", "/api/login", body={"login": "student-one", "password": "strong-test-password"}
    )
    assert student.call("GET", "/api/group-view/Group-B")[0] in (403, 404)
    own = student.call("GET", "/api/group-view/Group-A")[1]
    assert not own["can_students"] and own["students"] == []
    assert student.call("GET", "/api/admin/group-students/Group-A?q=student")[0] == 403
    assert student.call("PUT", "/api/admin/group-assistants/Group-A", body={"ta_ids": []})[0] == 403
    assert (
        c.call(
            "PUT",
            "/api/admin/group-assistants/Group-A",
            body={"ta_ids": [found[0]["id"]]},
            csrf=False,
        )[0]
        == 400
    )
    assert (
        c.call("PUT", "/api/admin/group-assistants/Group-A", body={"ta_ids": [found[0]["id"]]})[0]
        == 200
    )
    assert student.call("GET", "/api/group-view/Group-A")[1]["can_students"]


def test_directory_sorts_by_surname_before_pagination(tmp_path):
    from reel.web.accounts import directory

    d = DB(tmp_path / "sort.db")
    for i, name in enumerate(["Արամ Սարգսյան", "Զարա Աբգարյան", "Անի Գրիգորյան"]):
        create_user(
            d,
            "sort-user-" + str(i),
            "strong-test-password",
            dict(name=name, email=f"sort{i}@example.test", role="admin", groups=[]),
        )
    page = directory(d, {}, dict(tab="admin", page_size=2))
    assert [r["name"] for r in page["items"]] == ["Զարա Աբգարյան", "Անի Գրիգորյան"]
    assert (
        directory(d, {}, dict(tab="admin", page_size=2, page=2))["items"][0]["name"]
        == "Արամ Սարգսյան"
    )
    d.conn.close()


def test_recording_defaults_read_persisted_subject_dates(web):
    import json

    d = DB(web.root / "reel.db")
    d.conn.execute(
        "INSERT INTO group_metadata(name,subjects_json,ta_ids_json,updated_at) VALUES(?,?,?,?)",
        (
            "Group-B",
            json.dumps(
                [
                    dict(name="Python", start_date="2020-01-01", end_date="2020-12-31"),
                    dict(name="JavaScript", start_date="2026-01-01", end_date="2099-12-31"),
                ]
            ),
            "[]",
            "2026-11-21",
        ),
    )
    d.conn.close()
    status, r = web.client("uploader").call("GET", "/api/recordings/defaults?group=Group-B")
    assert status == 200 and r["subject"] == "JavaScript" and r["current"] == "JavaScript"


def test_explicit_course_association_is_admin_only_and_used_in_cards(web):
    c = web.client("admin")
    assert (
        c.call(
            "PUT",
            "/api/admin/group-course/Group-A",
            body={"course_id": "conscripts-atmk"},
            csrf=False,
        )[0]
        == 400
    )
    assert c.call("PUT", "/api/admin/group-course/Group-A", body={"course_id": "unknown"})[0] == 400
    assert (
        c.call("PUT", "/api/admin/group-course/Group-A", body={"course_id": "conscripts-atmk"})[0]
        == 200
    )
    assert c.call("GET", "/api/group-view/Group-A")[1]["course_id"] == "conscripts-atmk"
    rows = c.call("GET", "/api/courses")[1]["courses"]
    assert [g["name"] for g in next(r for r in rows if r["id"] == "conscripts-atmk")["groups"]] == [
        "Group-A"
    ]


def test_junior_consent_uses_explicit_course_even_for_neutral_group_names(tmp_path):
    from fastapi import HTTPException

    from reel.web.privacy import junior, validate

    d = DB(tmp_path / "junior.db")
    d.conn.execute("INSERT INTO group_course_links VALUES('2.7','ai-junior')")
    assert junior({"role": "student", "groups": ["2.7"]}, d.conn)
    with pytest.raises(HTTPException):
        validate({"recording_consent": True}, {"role": "student", "groups": ["2.7"]}, d.conn)
    d.conn.close()
