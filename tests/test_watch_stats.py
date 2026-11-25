"""Named watch analytics: temp-only data, genuine private library scope and per-group TA rights."""

import json
from contextlib import closing
from urllib.parse import quote

import pytest

from reel import learning
from reel import watch_statistics as stats
from reel.db import DB
from reel.insights import library_records
from reel.web import accounts

PASSWORD = "watch-statistics-fixture-password"
STAMP = "2026-11-20T10:00:00+00:00"


@pytest.fixture
def database(tmp_path):
    d = DB(tmp_path / "stats.db")
    try:
        yield d
    finally:
        d.conn.close()


def user(d, login, role="student", groups=("Group-A",), **flags):
    uid = accounts.create_user(
        d,
        login,
        PASSWORD,
        {
            "name": "Անուն " + login,
            "email": login + "@example.test",
            "role": role,
            "groups": list(groups) if role != "admin" else [],
        },
    )
    if flags:
        d.conn.execute(
            "UPDATE users SET " + ",".join(key + "=?" for key in flags) + " WHERE id=?",
            (*flags.values(), uid),
        )
    return uid


def principal(uid, role="admin", groups=()):
    return {"id": uid, "role": role, "groups": list(groups)}


def lesson(
    d,
    key,
    group="Group-A",
    published="2026-11-20T00:00:00+00:00",
    *,
    duration=100,
    privacy=True,
    url=None,
    publish=True,
):
    jid = d.create_job("/fixture/" + key + ".mp4", key, group, "Python", lesson_date="2026-01-01")
    for name, output in [
        ("metadata", {"title": "Դաս " + key, "chapters": []}),
        ("segments", {"final_sec": duration}),
    ]:
        d.stage_start(jid, name)
        d.stage_done(jid, name, output)
    if privacy:
        d.stage_start(jid, "privacy")
        d.stage_done(jid, "privacy", {})
    if publish:
        d.add_publication(
            jid, "youtube", str(jid).zfill(11), url or "https://youtu.be/" + str(jid).zfill(11)
        )
        d.conn.execute(
            "UPDATE publications SET created_at=? WHERE job_id=? AND target='youtube'",
            (published, jid),
        )
    return jid


def watched(d, uid, jid, seconds, duration=100, updated=STAMP, position=None):
    learning.ensure_schema(d.conn)
    d.conn.execute(
        "INSERT INTO watch_progress(user_id,job_id,position,duration,watched_sec,intervals,completed,first_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            uid,
            jid,
            seconds if position is None else position,
            duration,
            seconds,
            json.dumps([[0, seconds]]) if seconds else "[]",
            int(seconds >= duration * 0.9) if duration else 0,
            updated,
            updated,
        ),
    )


def assign_ta(d, name, ids):
    d.conn.execute(
        "CREATE TABLE IF NOT EXISTS group_metadata(name TEXT PRIMARY KEY,ta_ids_json TEXT NOT NULL DEFAULT '[]')"
    )
    columns = {row[1] for row in d.conn.execute("PRAGMA table_info(group_metadata)")}
    values = {"name": name, "ta_ids_json": json.dumps(ids)}
    if "updated_at" in columns:
        values["updated_at"] = STAMP
    d.conn.execute(
        "INSERT INTO group_metadata("
        + ",".join(values)
        + ") VALUES("
        + ",".join("?" for _ in values)
        + ") "
        "ON CONFLICT(name) DO UPDATE SET ta_ids_json=excluded.ta_ids_json",
        tuple(values.values()),
    )


def test_exact_percentages_enrolled_students_only_and_zero_watch_pair(database):
    admin = user(database, "administrator", "admin")
    anna = user(database, "anna")
    boris = user(database, "boris")
    outsider = user(database, "outsider", groups=("Group-B",))
    inactive = user(database, "inactive", active=0)
    removed = user(database, "removed", removed_at=STAMP)
    deleted = user(database, "deleted", deleted_at=STAMP)
    lecturer = user(database, "teacher", "lecturer")
    older = lesson(database, "older", published="2026-11-01T00:00:00+00:00")
    newest = lesson(database, "newest", published="2026-11-02T00:00:00+00:00")
    watched(database, anna, older, 90)
    watched(database, anna, newest, 25)
    watched(database, boris, newest, 0, position=99)  # A seek/resume location is not watched time.
    for uid in (outsider, inactive, removed, deleted, lecturer):
        watched(database, uid, older, 100)
    result = stats.statistics(
        database, library_records(database), principal(admin), known_groups=["Group-A", "Group-B"]
    )
    assert [row["job_id"] for row in result["lessons"]] == [newest, older]
    assert result["lessons"][0]["students"] == 2 and result["lessons"][0]["average_percent"] == 12.5
    assert result["lessons"][1]["started"] == 1 and result["lessons"][1]["completed"] == 1
    assert result["lessons"][1]["average_percent"] == 45
    by_id = {row["id"]: row for row in result["students"]}
    assert set(by_id) == {anna, boris, outsider}
    assert (
        by_id[anna]["percent"] == 57.5
        and by_id[anna]["watched_sec"] == 115
        and by_id[anna]["completed"] == 1
    )
    assert by_id[anna]["last_watched_at"] == STAMP and by_id[anna]["total_lessons"] == 2
    assert by_id[boris]["percent"] == 0 and by_id[boris]["completed"] == 0
    assert [row["id"] for row in result["missed"]] == [boris]
    assert {row["job_id"] for row in result["missed"][0]["lessons"]} == {older, newest}
    serialized = json.dumps(result)
    assert (
        "@example.test" not in serialized
        and "password_hash" not in serialized
        and "intervals" not in serialized
    )


def test_missing_last_pair_uses_publication_time_not_job_order_or_lesson_date(database):
    admin = user(database, "administrator", "admin")
    student = user(database, "student")
    old = lesson(database, "first-created", published="2026-11-01T00:00:00+00:00")
    latest = lesson(database, "second-created", published="2026-11-03T02:00:00+04:00")
    middle = lesson(database, "third-created", published="2026-11-02T10:00:00Z")
    watched(database, student, old, 100)
    result = stats.statistics(database, library_records(database), principal(admin))
    assert [row["job_id"] for row in result["missed"][0]["lessons"]] == [latest, middle]
    database.conn.execute("DELETE FROM watch_progress WHERE user_id=?", (student,))
    watched(database, student, latest, 0.2)
    assert stats.statistics(database, library_records(database), principal(admin))["missed"] == []


def test_one_lesson_does_not_create_a_missed_pair(database):
    admin = user(database, "administrator", "admin")
    user(database, "student")
    lesson(database, "only")
    assert stats.statistics(database, library_records(database), principal(admin))["missed"] == []


def test_admin_all_lecturer_own_scope_and_group_filter_keeps_selector_options(database):
    admin = user(database, "administrator", "admin")
    teacher = user(database, "teacher", "lecturer")
    a = user(database, "student-a")
    b = user(database, "student-b", groups=("Group-B",))
    first = lesson(database, "a")
    lesson(database, "b", group="Group-B")
    records = library_records(database)
    selected = stats.statistics(
        database, records, principal(admin), group="Group-A", known_groups=["Group-A", "Group-B"]
    )
    assert selected["groups"] == [{"name": "Group-A"}, {"name": "Group-B"}]
    assert selected["selected_group"] == "Group-A" and [
        row["job_id"] for row in selected["lessons"]
    ] == [first]
    assert [row["id"] for row in selected["students"]] == [a]
    # Cached principal claims never replace the authoritative account role/membership.
    own = stats.statistics(
        database,
        records,
        principal(teacher, "admin", ["Group-B"]),
        known_groups=["Group-A", "Group-B"],
    )
    assert own["groups"] == [{"name": "Group-A"}] and all(row["id"] != b for row in own["students"])
    with pytest.raises(LookupError):
        stats.statistics(database, records, principal(teacher), group="Group-B")


def test_ta_rights_are_exact_group_membership_and_do_not_create_general_staff_rights(database):
    ta = user(database, "assistant")
    other = user(database, "other", groups=("Group-B",))
    lesson(database, "a")
    lesson(database, "b", group="Group-B")
    assert stats.available(database, principal(ta)) is False  # Optional group metadata is absent.
    assign_ta(database, "Group-B", [ta])
    assert stats.available(database, principal(ta)) is False
    assign_ta(database, "Group-A", [ta])
    result = stats.statistics(
        database, library_records(database), principal(ta, "student", ["Group-A"])
    )
    assert result["groups"] == [{"name": "Group-A"}]
    assert all(row["id"] != other for row in result["students"])
    with pytest.raises(LookupError):
        stats.statistics(database, library_records(database), principal(ta), group="Group-B")
    assert (
        database.conn.execute("SELECT role FROM users WHERE id=?", (ta,)).fetchone()[0] == "student"
    )
    database.conn.execute("UPDATE group_metadata SET ta_ids_json='[]' WHERE name='Group-A'")
    assert stats.available(database, principal(ta)) is False


@pytest.mark.parametrize(
    "kind",
    [
        "shared-admin",
        "shared-viewer",
        "shared-uploader",
        "shared-student",
        "ordinary-student",
        "inactive",
        "removed",
        "deleted",
        "missing",
    ],
)
def test_ineligible_principals_cannot_access_named_statistics(database, kind):
    uid = user(database, "candidate", "student")
    p = principal(uid, "admin", ["Group-A"])
    if kind.startswith("shared-"):
        p = {"role": kind[7:], "legacy": True, "groups": []}
    elif kind == "inactive":
        database.conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    elif kind == "removed":
        database.conn.execute("UPDATE users SET removed_at=? WHERE id=?", (STAMP, uid))
    elif kind == "deleted":
        database.conn.execute("UPDATE users SET deleted_at=? WHERE id=?", (STAMP, uid))
    elif kind == "missing":
        p["id"] = 9999
    assert stats.available(database, p) is False
    with pytest.raises(PermissionError):
        stats.statistics(database, [], p, known_groups=["Group-A"])


def test_missing_progress_tables_return_zero_data_without_creating_tables(database):
    admin = user(database, "administrator", "admin")
    student = user(database, "student")
    lesson(database, "a")
    lesson(database, "b")
    before = {
        row[0] for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "watch_progress" not in before
    result = stats.statistics(database, library_records(database), principal(admin))
    assert (
        all(row["percent"] == 0 for row in result["students"])
        and result["missed"][0]["id"] == student
    )
    assert {
        row[0] for row in database.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } == before
    database.conn.execute(
        "CREATE TABLE watch_progress(user_id INTEGER,job_id INTEGER,position REAL)"
    )
    assert (
        stats.statistics(database, library_records(database), principal(admin))["lessons"][0][
            "started"
        ]
        == 0
    )


def test_canonical_duration_clamps_bad_values_and_completion_does_not_round_up(database):
    admin = user(database, "administrator", "admin")
    student = user(database, "student")
    first = lesson(database, "a")
    second = lesson(database, "b")
    watched(database, student, first, 89.96, duration=1)
    watched(database, student, second, 5000, duration=1)
    result = stats.statistics(database, library_records(database), principal(admin))
    by_id = {row["job_id"]: row for row in result["students"][0]["lessons"]}
    assert by_id[first]["percent"] == 90 and by_id[first]["completed"] is False
    assert (
        by_id[second]["watched_sec"] == 100
        and by_id[second]["percent"] == 100
        and by_id[second]["completed"]
    )
    database.conn.execute(
        "UPDATE watch_progress SET watched_sec=? WHERE job_id=?", (float("inf"), second)
    )
    assert (
        stats.statistics(database, library_records(database), principal(admin))["students"][0][
            "completed"
        ]
        == 0
    )


def test_core_never_adds_demo_or_non_youtube_rows(database):
    admin = user(database, "administrator", "admin")
    user(database, "student")
    jid = lesson(database, "real")
    real = library_records(database)
    extra = [
        {**real[0], "job_id": 1000, "demo": True},
        {**real[0], "job_id": 1001, "url": "https://example.test/video"},
        {**real[0], "job_id": 1002, "has_video": False},
    ]
    assert [
        row["job_id"]
        for row in stats.statistics(database, real + extra, principal(admin))["lessons"]
    ] == [jid]


def test_http_route_scope_privacy_progress_and_no_other_operational_rights(web):
    d = DB(web.root / "reel.db")
    with closing(d.conn):
        user(d, "http-teacher", "lecturer")
        ta = user(d, "http-ta")
        student = user(d, "http-student")
        outsider = user(d, "http-outsider", groups=("Group-B",))
        first = lesson(d, "public-a")
        second = lesson(d, "public-b", group="Group-B")
        lesson(d, "private-a", privacy=False)
        lesson(d, "not-published", publish=False)
        lesson(d, "not-youtube", url="https://example.test/video")
        watched(d, student, first, 95)
        watched(d, outsider, first, 100)
        assign_ta(d, "Group-A", [ta])

    def logged_in(login):
        client = web.client()
        assert (
            client.call("POST", "/api/login", body={"login": login, "password": PASSWORD})[0] == 200
        )
        return client

    client = logged_in("http-teacher")
    code, result = client.call("GET", "/api/library/watch-stats")
    assert code == 200, result
    assert result["groups"] == [{"name": "Group-A"}]
    assert [row["job_id"] for row in result["lessons"]] == [first]
    assert result["lessons"][0]["students"] == 2 and result["lessons"][0]["completed"] == 1
    assert {row["id"] for row in result["students"]} == {ta, student}
    assert client.call("GET", "/api/library/watch-stats?group=Group-B")[0] == 404
    assert logged_in("http-student").call("GET", "/api/library/watch-stats")[0] == 403
    ta_client = logged_in("http-ta")
    assert ta_client.call("GET", "/api/library/watch-stats")[0] == 200
    assert ta_client.call("GET", "/api/jobs")[0] == 403
    assert ta_client.call("POST", f"/api/jobs/{first}/retry")[0] == 403
    assert ta_client.call("GET", "/api/accounts/users")[0] == 403
    for who in ("viewer", "uploader"):
        assert web.client(who).call("GET", "/api/library/watch-stats")[0] == 403
    assert web.client().call("GET", "/api/library/watch-stats")[0] == 401
    admin = web.client("admin")
    result = admin.call("GET", "/api/library/watch-stats?group=" + quote("Group-B"))[1]
    assert result["groups"] == [{"name": "Group-A"}, {"name": "Group-B"}]
    assert [row["job_id"] for row in result["lessons"]] == [second]
    assert "@example.test" not in json.dumps(result)
