"""Academy presentation contract: temporary data, honest demo metrics, no operational-rights expansion."""

import json
from contextlib import closing
from datetime import UTC, date, datetime

import pytest

from reel import academy
from reel.db import DB
from reel.web import accounts
from reel.web.academy_api import make_router

NOW = date(2026, 11, 21)
PASSWORD = "academy-fixture-password"
CONFIG = {
    "groups": {
        "Group-A": {
            "subjects": [{"name": "Python", "playlist": "PLpy"}, {"name": "Math", "playlist": ""}],
            "slack_channel": "C1",
            "lecturers": [],
        },
        "Group-B": {
            "subjects": [{"name": "JavaScript", "playlist": "PLjs"}],
            "slack_channel": "C2",
            "lecturers": [],
        },
        "Empty": {
            "subjects": [{"name": "Rust", "playlist": ""}],
            "slack_channel": "C3",
            "lecturers": [],
        },
        "Demo": {
            "subjects": [{"name": "Design", "playlist": ""}],
            "slack_channel": "DEMO",
            "lecturers": [],
        },
    },
    "operator_slack_channel": "Coperator",
    "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
}


@pytest.fixture
def data(tmp_path):
    d = DB(tmp_path / "reel.db")
    try:
        yield d, tmp_path
    finally:
        d.conn.close()


def user(d, login, role="lecturer", groups=("Group-A",), **flags):
    uid = accounts.create_user(
        d,
        login,
        PASSWORD,
        {
            "name": "Անուն " + login,
            "email": login + "@example.test",
            "role": role,
            "groups": [] if role == "admin" else list(groups),
        },
    )
    if flags:
        d.conn.execute(
            "UPDATE users SET " + ",".join(key + "=?" for key in flags) + " WHERE id=?",
            (*flags.values(), uid),
        )
    return {"id": uid, "role": role, "groups": list(groups)}


def job(
    d,
    key,
    group="Group-A",
    subject="Python",
    *,
    day="2026-11-20",
    status="done",
    published=True,
    privacy=True,
    source=7200,
    final=3600,
):
    jid = d.create_job("/private/fixture/" + key + ".mp4", key, group, subject, lesson_date=day)
    d.set_job(jid, status=status, error="Bearer private-job-error-do-not-return")
    stages = {
        "metadata": {"title": "Դաս " + key, "chapters": []},
        "segments": {"original_sec": source, "final_sec": final},
    }
    if source is not None:
        stages["probe"] = {"duration_sec": source}
    if privacy:
        stages["privacy"] = {}
    for name, output in stages.items():
        d.stage_start(jid, name)
        d.stage_done(jid, name, output)
    if published:
        d.add_publication(
            jid, "youtube", str(jid).zfill(11), "https://youtu.be/" + str(jid).zfill(11)
        )
    return jid


def demo(
    d,
    key,
    *,
    group="Demo",
    subject="Design",
    day="2026-11-20",
    duration=0,
    thumbnail="/brand/reel-email.png",
):
    d.conn.execute(
        "INSERT INTO demo_lessons(id,group_name,subject,title,description,lesson_date,thumbnail,duration_sec,demo_tag) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            key,
            group,
            subject,
            "Ցուցադրական " + key,
            "Մետատվյալներ",
            day,
            thumbnail,
            duration,
            "g40-demo-v1",
        ),
    )


def test_overview_counts_current_week_groups_and_demo_without_fabricating_work(data):
    d, root = data
    p = user(d, "admin", "admin")
    job(d, "published")
    job(d, "running", status="running", published=False, privacy=False)
    job(d, "queued", status="queued", published=False, privacy=False)
    job(d, "stopped", status="stopped", published=False, privacy=False)
    job(d, "last-week", day="2026-11-10")
    demo(d, "illustrative")
    d.conn.execute(
        "INSERT INTO group_metadata(name,demo_tag,updated_at) VALUES(?,?,?)",
        ("Demo", "g40-demo-v1", "2026-11-01"),
    )
    d.conn.execute(
        "INSERT INTO group_metadata(name,course_end,updated_at) VALUES(?,?,?)",
        ("Group-B", "2026-11-01", "2026-11-01"),
    )
    before = list(d.conn.iterdump())
    result = academy.overview(d, CONFIG, root, p, now=NOW)
    assert (
        result["cards"]["active_groups"] == 3
        and result["actual"]["active_groups"] == 2
        and result["demo"]["active_groups"] == 1
    )
    assert (
        result["cards"]["week_lessons"] == 5
        and result["actual"]["week_lessons"] == 4
        and result["demo"]["week_lessons"] == 1
    )
    assert result["cards"]["processing"] == 1 and result["cards"]["queued"] == 1
    assert result["cards"]["published_lessons"] == 2 and result["demo"]["published_lessons"] == 0
    assert result["week_start"] == "2026-11-16" and result["week_end"] == "2026-11-22"
    assert len(result["processing"]) == 2 and all(row["thumbnail"] for row in result["latest"])
    assert len(result["latest"]) == 3 and all(
        row["published"] or row["demo"] for row in result["latest"]
    )
    assert result["warnings"][0]["kind"] == "stopped"
    assert "private-job-error" not in json.dumps(result) and "/private/fixture/" not in json.dumps(
        result
    )
    assert list(d.conn.iterdump()) == before


def test_overview_yerevan_week_boundary_and_completed_scope(data):
    d, root = data
    p = user(d, "lecturer")
    job(d, "monday", day="2026-11-16")
    job(d, "sunday", day="2026-11-15")
    result = academy.overview(d, CONFIG, root, p, now=datetime(2026, 11, 15, 21, tzinfo=UTC))
    assert result["week_start"] == "2026-11-16" and result["cards"]["week_lessons"] == 1
    assert "warnings" not in result


def test_lesson_filters_include_zero_choices_and_published_privacy_gate(data):
    d, root = data
    p = user(d, "admin", "admin")
    real = job(d, "real")
    unsafe = job(d, "privacy-pending", privacy=False)
    running = job(d, "processing", status="running", published=False)
    demo(d, "metadata-only", thumbnail="https://attacker.invalid/private")
    result = academy.lessons(d, CONFIG, root, p)
    assert result["total"] == 4
    by_id = {row["job_id"]: row for row in result["items"] if row["job_id"] is not None}
    assert by_id[real]["status"] == "published" and by_id[real]["playable"]
    assert (
        by_id[unsafe]["status"] == "done"
        and by_id[unsafe]["url"] is None
        and not by_id[unsafe]["published"]
    )
    assert by_id[running]["status"] == "processing"
    illustrative = next(row for row in result["items"] if row["demo"])
    assert (
        illustrative["status"] == "demo"
        and illustrative["job_id"] is None
        and not illustrative["playable"]
    )
    assert (
        illustrative["url"] is None
        and illustrative["detail_url"] is None
        and illustrative["thumbnail"] == academy.PLACEHOLDER
    )
    choices = {row["name"]: row for row in result["filters"]["groups"]}
    assert choices["Empty"] == {
        "name": "Empty",
        "count": 0,
        "subjects": [{"name": "Rust", "count": 0}],
    }
    assert (
        next(row for row in choices["Group-A"]["subjects"] if row["name"] == "Math")["count"] == 0
    )
    assert (
        len(
            academy.lessons(
                d, CONFIG, root, p, group="Group-A", subject="Python", status="processing"
            )["items"]
        )
        == 1
    )
    assert academy.lessons(d, CONFIG, root, p, subject="No such subject")["items"] == []
    with pytest.raises(ValueError):
        academy.lessons(d, CONFIG, root, p, status="invalid")


def test_private_thumbnail_files_never_surface_before_privacy(data):
    d, root = data
    p = user(d, "lecturer")
    unsafe = job(d, "unsafe", published=False, privacy=False)
    safe = job(d, "safe", published=False)
    for jid in (unsafe, safe):
        folder = root / "work" / str(jid)
        folder.mkdir(parents=True)
        (folder / "thumbnail.jpg").write_bytes(b"fixture")
    rows = {row["job_id"]: row for row in academy.lessons(d, CONFIG, root, p)["items"]}
    assert (
        rows[unsafe]["thumbnail"] == academy.PLACEHOLDER
        and rows[safe]["thumbnail"] == f"/api/jobs/{safe}/thumbnail"
    )
    (root / "work" / str(safe) / "thumbnail.jpg").unlink()
    (root / "work" / str(safe) / "thumbnail.jpg").symlink_to(
        root / "work" / str(unsafe) / "thumbnail.jpg"
    )
    assert (
        next(row for row in academy.lessons(d, CONFIG, root, p)["items"] if row["job_id"] == safe)[
            "thumbnail"
        ]
        == academy.PLACEHOLDER
    )


def test_statistics_actual_and_demo_counts_hours_savings_and_daily_graph_stay_separate(data):
    d, root = data
    p = user(d, "admin", "admin")
    job(d, "real", source=7200, final=3600)
    job(d, "unpublished", published=False, source=10000, final=9000)
    job(d, "privacy-pending", privacy=False, source=10000, final=9000)
    demo(d, "duration-unknown")
    demo(d, "illustrative-hour", duration=1800)
    result = academy.statistics(d, CONFIG, root, p, now=NOW)
    assert (
        result["cards"]
        == result["actual"]
        == {"published_lessons": 1, "video_hours": 1, "saved_minutes": 65}
    )
    assert result["demo"] == {"lessons": 2, "video_hours": 0.5, "saved_minutes": 0}
    assert (
        len(result["daily"]) == 30
        and result["period_end"] == "2026-11-21"
        and result["date_basis"] == "lesson_date"
    )
    day = next(row for row in result["daily"] if row["date"] == "2026-11-20")
    assert day == {
        "date": "2026-11-20",
        "actual_lessons": 1,
        "demo_lessons": 2,
        "actual_hours": 1,
        "demo_hours": 0.5,
    }
    group = next(row for row in result["by_group"] if row["group"] == "Group-A")
    assert group["actual"]["published_lessons"] == 1 and group["demo"]["lessons"] == 0
    assert next(row for row in result["by_group"] if row["group"] == "Empty")["lessons"] == 0
    assert next(row for row in result["by_subject"] if row["subject"] == "Math")["lessons"] == 0
    assert "technical" in result


def test_missing_source_duration_does_not_invent_editing_savings(data):
    d, root = data
    p = user(d, "lecturer")
    job(d, "no-source", source=None, final=600)
    result = academy.statistics(d, CONFIG, root, p, now=NOW)
    assert result["actual"] == {"published_lessons": 1, "video_hours": 0.167, "saved_minutes": 5}


def test_scope_and_technical_projection_use_actual_memberships_and_filtered_jobs(data, monkeypatch):
    d, root = data
    teacher = user(d, "teacher")
    admin = user(d, "admin", "admin")
    job(d, "python")
    job(d, "math", subject="Math")
    job(d, "other-group", group="Group-B", subject="JavaScript")
    demo(d, "outside")

    def forbidden(*args, **kwargs):
        pytest.fail("nonadmin invoked technical aggregator")

    original = academy.insights.statistics
    monkeypatch.setattr(academy.insights, "statistics", forbidden)
    result = academy.statistics(
        d, CONFIG, root, {**teacher, "role": "admin", "groups": ["Group-B"]}, now=NOW
    )
    assert {row["group"] for row in result["by_group"]} == {"Group-A"} and "technical" not in result
    assert "warnings" not in academy.overview(d, CONFIG, root, teacher, now=NOW)
    with pytest.raises(LookupError):
        academy.lessons(d, CONFIG, root, teacher, group="Group-B")
    monkeypatch.setattr(academy.insights, "statistics", original)
    selected = academy.statistics(d, CONFIG, root, admin, group="Group-A", subject="Math", now=NOW)
    assert selected["actual"]["published_lessons"] == 1
    assert selected["technical"]["counts"] == {"done": 1}
    assert selected["technical"]["groups"][0]["subject"] == "Math"


@pytest.mark.parametrize(
    "kind", ["student", "ta", "inactive", "removed", "deleted", "missing", "legacy-student"]
)
def test_student_ta_and_invalid_accounts_gain_no_operational_access(data, kind):
    d, root = data
    p = user(d, "candidate", "student" if kind in ("student", "ta") else "lecturer")
    if kind == "ta":
        d.conn.execute(
            "INSERT INTO group_metadata(name,ta_ids_json,updated_at) VALUES(?,?,?)",
            ("Group-A", json.dumps([p["id"]]), "2026-11-01"),
        )
    elif kind == "inactive":
        d.conn.execute("UPDATE users SET active=0 WHERE id=?", (p["id"],))
    elif kind in ("removed", "deleted"):
        d.conn.execute("UPDATE users SET " + kind + "_at=? WHERE id=?", ("2026-11-01", p["id"]))
    elif kind == "missing":
        p["id"] = 9999
    elif kind == "legacy-student":
        p = {"role": "student", "legacy": True, "groups": []}
    for fn in (academy.overview, academy.lessons, academy.statistics):
        with pytest.raises(PermissionError):
            fn(d, CONFIG, root, p)


def test_legacy_viewer_uploader_keep_existing_scope_but_never_technical_or_warnings(data):
    d, root = data
    job(d, "a")
    job(d, "b", group="Group-B")
    for role in ("viewer", "uploader"):
        p = {"role": role, "legacy": True, "groups": []}
        assert academy.lessons(d, CONFIG, root, p)["total"] == 2
        assert "technical" not in academy.statistics(d, CONFIG, root, p)
        assert "warnings" not in academy.overview(d, CONFIG, root, p)


def test_api_can_mount_per_checkpoint_and_real_http_scope_is_unchanged(web):
    env = web.module.__dict__
    expected = {
        "overview": "/api/academy/overview",
        "lessons": "/api/academy/lessons",
        "stats": "/api/academy/stats",
    }
    for section, path in expected.items():
        assert [route.path for route in make_router(env, sections=(section,)).routes] == [path]
    # Insert only into this running temporary app, before its static catch-all.
    existing = {getattr(route, "path", None) for route in web.module.app.router.routes}
    missing = tuple(section for section, path in expected.items() if path not in existing)
    web.module.app.router.routes[-1:-1] = make_router(env, sections=missing).routes
    d = DB(web.root / "reel.db")
    with closing(d.conn):
        job(d, "http-a")
        job(d, "http-b", group="Group-B", subject="Python")
        demo(d, "http-demo", group="Group-A", subject="Group-A")
        user(d, "http-teacher")
        ta = user(d, "http-ta", "student")
        d.conn.execute(
            "INSERT INTO group_metadata(name,ta_ids_json,updated_at) VALUES(?,?,?)",
            ("Group-A", json.dumps([ta["id"]]), "2026-11-01"),
        )

    def login(name):
        c = web.client()
        assert c.call("POST", "/api/login", body={"login": name, "password": PASSWORD})[0] == 200
        return c

    teacher = login("http-teacher")
    student = login("http-ta")
    for path in expected.values():
        assert web.client().call("GET", path)[0] == 401
        assert student.call("GET", path)[0] == 403
        code, result = teacher.call("GET", path)
        assert code == 200, result
        assert (
            "Group-B" not in json.dumps(result)
            and "warnings" not in result
            and "technical" not in result
        )
        for role in ("viewer", "uploader"):
            assert web.client(role).call("GET", path)[0] == 200
    assert teacher.call("GET", "/api/academy/lessons?group=Group-B")[0] == 404
    assert teacher.call("GET", "/api/academy/lessons?status=invalid")[0] == 400
    admin = web.client("admin")
    assert "warnings" in admin.call("GET", "/api/academy/overview")[1]
    assert "technical" in admin.call("GET", "/api/academy/stats")[1]


def test_same_day_latest_orders_actual_jobs_numerically(data):
    d, root = data
    p = user(d, "admin", "admin")
    ids = [job(d, "same-day-" + str(index)) for index in range(12)]
    latest = academy.overview(d, CONFIG, root, p, now=NOW)["latest"]
    assert [row["job_id"] for row in latest] == list(reversed(ids))[:6]


@pytest.mark.parametrize(
    "raw,state", [("queued", "queued"), ("running", "processing"), ("stopped", "stopped")]
)
def test_workflow_state_remains_visible_after_youtube_publication(data, raw, state):
    d, root = data
    p = user(d, "admin", "admin")
    jid = job(d, "published-but-" + raw, status=raw)
    listed = academy.lessons(d, CONFIG, root, p, status=state)["items"]
    assert [row["job_id"] for row in listed] == [jid]
    assert listed[0]["status"] == state and listed[0]["job_status"] == raw
    assert listed[0]["published"] and listed[0]["playable"] and listed[0]["url"]
    overview = academy.overview(d, CONFIG, root, p, now=NOW)
    if raw == "stopped":
        warning = next(item for item in overview["warnings"] if item["kind"] == "stopped")
        assert warning["job_ids"] == [jid] and warning["href"] == "#/jobs?status=stopped"
    else:
        assert overview["processing"][0]["status"] == state
