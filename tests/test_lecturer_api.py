"""HTTP tests: source-exec app, ephemeral server and exclusively temporary data."""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
import uvicorn

from reel import config as config_module
from reel import pipeline
from reel.db import DB
from reel.web import admin, lecturer_api

PROJECT = Path(__file__).resolve().parents[1]
PASSWORDS = {role: f"lecturer-{role}-fixture" for role in ("admin", "viewer", "uploader")}
JPEG = b"\xff\xd8\xff\xe0isolated-jpeg-fixture\xff\xd9"


def auto_data():
    return {
        "mode": "unsure",
        "reason": "two candidates have similar presence",
        "face_frames": 70,
        "identities": [
            {
                "presence": 40,
                "share": 0.57,
                "face_px": 120,
                "first_t": 2,
                "last_t": 90,
                "centre": [999],
                "extra": {"centres": [[999]]},
            },
            {"presence": 30, "share": 0.43, "face_px": 80, "first_t": 8, "last_t": 60},
        ],
        "centres": [
            [float(i) / 1000 for i in range(128)],
            [float(i + 128) / 1000 for i in range(128)],
        ],
        "extra": {"centre": [999]},
    }


def assert_no_centres(value):
    if isinstance(value, dict):
        assert not {"centre", "centres"}.intersection(value)
        for child in value.values():
            assert_no_centres(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_centres(child)


def endpoint(job_id, name=None):
    path = f"/api/jobs/{job_id}/lecturer"
    return path if name is None else path + "/" + quote(name, safe="")


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, *, csrf=True, raw=None):
        headers = {"X-Requested-With": "reel" if csrf is True else csrf} if csrf else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/json"
        try:
            response = self.opener.open(
                Request(self.base + path, data=data, headers=headers, method=method), timeout=10
            )
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
        parsed = json.loads(payload) if payload[:1] in (b"{", b"[") else payload
        if isinstance(parsed, (dict, list)):
            assert_no_centres(parsed)
        return response.code, parsed


def metadata_snapshot():
    # Metadata only: never open the production DB or walk/read its work directory.
    return {
        name: (PROJECT / name).stat().st_mtime_ns if (PROJECT / name).exists() else None
        for name in ("reel.db", "work")
    }


@pytest.fixture(scope="module", autouse=True)
def production_metadata_is_unchanged():
    before = metadata_snapshot()
    yield
    assert metadata_snapshot() == before, (
        "production DB/work metadata changed while isolated tests ran"
    )


def new_job(root, *, status="stopped", blur="failed", output=None, publication=None):
    database = DB(root / "reel.db")
    try:
        job_id = database.create_job(
            str(root / "archive" / "lesson.mp4"), uuid4().hex, "Group-A", "Python"
        )
        if blur != "pending":
            database.stage_start(job_id, "blur")
            if output is not None or blur == "done":
                database.stage_done(job_id, "blur", output)
            if blur == "failed":
                database.stage_failed(job_id, "blur", "lecturer selection is uncertain")
        if publication:
            database.add_publication(job_id, publication, None, None)
        database.set_job(job_id, status=status)
        return job_id
    finally:
        database.conn.close()


def put_auto(root, job_id, data=None):
    path = root / "work" / str(job_id) / "auto_refs"
    path.mkdir(parents=True, exist_ok=True)
    (path / "auto.json").write_text(
        json.dumps(auto_data() if data is None else data), encoding="utf-8"
    )
    for name in ("identity_1.jpg", "identity_2.jpg", "ref_0.jpg", "ref_12.jpg"):
        (path / name).write_bytes(JPEG)
    return path


@pytest.fixture
def web(tmp_path, monkeypatch):
    protected = [
        PROJECT / "work",
        PROJECT / "secrets",
        PROJECT / "config.yaml",
        PROJECT / "config.yaml.bak",
        PROJECT / "reel.db",
        PROJECT / "reel.db-wal",
        PROJECT / "reel.db-shm",
    ]
    forbidden_accesses = []

    def check_path(value):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        candidate = Path(os.path.abspath(os.fsdecode(value)))
        for path in (candidate, candidate.resolve()):
            if any(path == base or path.is_relative_to(base) for base in protected):
                forbidden_accesses.append(str(path))
                raise AssertionError(f"production data access blocked: {path}")

    def guarded(original):
        def call(path, *args, **kwargs):
            check_path(path)
            return original(path, *args, **kwargs)

        return call

    def guarded_move(original):
        def call(source, destination, *args, **kwargs):
            check_path(source)
            check_path(destination)
            return original(source, destination, *args, **kwargs)

        return call

    for owner, name in (
        (builtins, "open"),
        (io, "open"),
        (os, "open"),
        (os, "mkdir"),
        (os, "unlink"),
        (os, "remove"),
        (os, "rmdir"),
        (os, "listdir"),
        (os, "scandir"),
    ):
        monkeypatch.setattr(owner, name, guarded(getattr(owner, name)))
    for name in ("replace", "rename"):
        monkeypatch.setattr(os, name, guarded_move(getattr(os, name)))

    original_db_init = DB.__init__
    original_resolve = config_module._resolve

    def isolated_db(self, path="reel.db"):
        assert Path(path).resolve() == tmp_path / "reel.db", "nonfixture DB access"
        original_db_init(self, path)

    def isolated_config(path):
        target = original_resolve(path)
        assert target.resolve().is_relative_to(tmp_path), "nonfixture config access"
        return target

    monkeypatch.setattr(DB, "__init__", isolated_db)
    monkeypatch.setattr(config_module, "_resolve", isolated_config)
    monkeypatch.setattr(lecturer_api, "ROOT", tmp_path, raising=False)
    monkeypatch.setattr(admin, "ROOT", tmp_path)
    notes = []
    monkeypatch.setattr(pipeline, "_notify", notes.append)

    def no_retry(*args, **kwargs):
        raise AssertionError("the lecturer endpoint must not retry or run a job")

    monkeypatch.setattr(pipeline, "retry", no_retry)
    for role, password in PASSWORDS.items():
        monkeypatch.setenv(f"REEL_{role.upper()}_PASSWORD", password)
    source = PROJECT / "reel" / "web" / "app.py"
    fake = tmp_path / "reel" / "web" / "app.py"
    (fake.parent / "static").mkdir(parents=True)
    (fake.parent / "static" / "index.html").write_text("lecturer HTTP fixture")
    module = ModuleType(f"_reel_lecturer_web_{uuid4().hex}")
    module.__file__ = str(fake)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source.read_text(encoding="utf-8"), str(fake), "exec"), module.__dict__)
    from account_fixture import existing_admin

    existing_admin(module)
    assert module.ROOT == lecturer_api.ROOT == tmp_path
    job_id = new_job(tmp_path)
    auto_refs = put_auto(tmp_path, job_id)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(
        uvicorn.Config(module.app, log_level="critical", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"

        def client(role=None):
            result = Client(base)
            if role:
                code, response = result.call(
                    "POST",
                    "/api/login",
                    {
                        "login": "fixture-admin" if role == "admin" else "",
                        "password": PASSWORDS[role],
                    },
                )
                assert code == 200 and response["role"] == role
            return result

        yield SimpleNamespace(
            root=tmp_path,
            job_id=job_id,
            auto_refs=auto_refs,
            client=client,
            choice=auto_refs.parent / "lecturer_choice.json",
            notes=notes,
        )
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
        assert forbidden_accesses == []


def job_snapshot(web):
    database = DB(web.root / "reel.db")
    try:
        return dict(database.job(web.job_id)), dict(database.stage(web.job_id, "blur"))
    finally:
        database.conn.close()


def job_files(web):
    return {
        p.relative_to(web.auto_refs.parent): p.read_bytes()
        for p in web.auto_refs.parent.rglob("*")
        if p.is_file()
    }


def test_viewer_uploader_and_anonymous_cannot_read_or_choose_lecturer_or_images(web):
    for role, expected in (("viewer", 403), ("uploader", 403), (None, 401)):
        client = web.client(role)
        for method, name, body in (
            ("GET", None, None),
            ("POST", None, {"none": True}),
            ("GET", "identity_1.jpg", None),
        ):
            code, response = client.call(
                method, endpoint(web.job_id, name), body, csrf=name is None
            )
            assert code == expected, (role, method, name, response)
    assert not web.choice.exists()


@pytest.mark.parametrize("csrf", [False, "XMLHttpRequest"])
def test_json_get_and_post_require_exact_csrf_but_admin_images_do_not(web, csrf):
    client = web.client("admin")
    assert client.call("GET", endpoint(web.job_id), csrf=csrf)[0] == 400
    assert client.call("POST", endpoint(web.job_id), {"none": True}, csrf=csrf)[0] == 400
    code, body = client.call("GET", endpoint(web.job_id, "identity_1.jpg"), csrf=False)
    assert code == 200 and body == JPEG
    assert not web.choice.exists()


def test_unsure_summary_whitelists_identity_fields_and_never_exposes_nested_embeddings(web):
    web.choice.write_text(
        json.dumps(
            {
                "identity": 2,
                "centre": auto_data()["centres"][1],
                "extra": {"nested": {"centres": [[999]]}},
            }
        )
    )
    code, response = web.client("admin").call("GET", endpoint(web.job_id))
    assert code == 200
    assert response["job_status"] == "stopped" and response["blur_status"] == "failed"
    assert response["mode"] == "unsure" and response["can_choose"] is True
    assert response["reason"] == auto_data()["reason"] and response["choice"] == {"identity": 2}
    assert response["lecturer_rank"] is None
    assert response["refs"] == ["ref_0.jpg", "ref_12.jpg"]
    assert [c["rank"] for c in response["identities"]] == [1, 2]
    assert (
        response["identities"][0]["presence"] == 40 and response["identities"][0]["share"] == 0.57
    )
    assert all(
        set(identity) == {"rank", "presence", "share", "face_px", "first_t", "last_t", "image"}
        for identity in response["identities"]
    )
    assert response["identities"][1]["image"] == "identity_2.jpg"


def test_missing_identity_image_is_reported_as_null(web):
    (web.auto_refs / "identity_2.jpg").unlink()
    code, response = web.client("admin").call("GET", endpoint(web.job_id))
    assert code == 200 and response["identities"][1]["image"] is None


@pytest.mark.parametrize("mode", ["auto", "chosen", "none", "unsure"])
def test_reads_all_discovery_modes_without_forwarding_private_centres(web, mode):
    data = auto_data()
    data.update(mode=mode, lecturer_rank=2 if mode in ("auto", "chosen") else None)
    (web.auto_refs / "auto.json").write_text(json.dumps(data))
    code, response = web.client("admin").call("GET", endpoint(web.job_id))
    assert (
        code == 200
        and response["mode"] == mode
        and response["lecturer_rank"] == data["lecturer_rank"]
    )


@pytest.mark.parametrize(
    "status,blur,publication,expected",
    [
        ("queued", "pending", None, True),
        ("queued", "running", None, True),
        ("stopped", "failed", None, True),
        ("stopped", "failed", "slack", True),
        ("running", "failed", None, False),
        ("done", "failed", None, False),
        ("queued", "done", None, False),
        ("stopped", "done", None, False),
        ("stopped", "failed", "youtube", False),
    ],
)
def test_can_choose_gate_matches_job_stage_and_youtube_publication(
    web, status, blur, publication, expected
):
    job_id = new_job(web.root, status=status, blur=blur, publication=publication)
    client = web.client("admin")
    code, response = client.call("GET", endpoint(job_id))
    assert code == 200 and response["can_choose"] is expected
    assert response["blur_status"] == blur
    code, response = client.call("POST", endpoint(job_id), {"none": True})
    assert code == (200 if expected else 409), response
    assert (web.root / "work" / str(job_id) / "lecturer_choice.json").exists() is expected


def test_identity_none_and_clear_persist_only_choice_without_retry_or_temporary_files(web):
    client, before_job, before_files = web.client("admin"), job_snapshot(web), job_files(web)
    code, response = client.call("POST", endpoint(web.job_id), {"identity": 2})
    assert code == 200 and response["ok"] is True and response["choice"] == {"identity": 2}
    saved = json.loads(web.choice.read_text())
    assert set(saved) == {"identity", "centre", "by", "at"}
    assert (
        saved["identity"] == 2
        and saved["centre"] == auto_data()["centres"][1]
        and saved["by"] == "admin"
    )
    assert datetime.fromisoformat(saved["at"].replace("Z", "+00:00")).utcoffset() == timedelta(0)
    assert set(job_files(web)) == set(before_files) | {Path("lecturer_choice.json")}
    assert client.call("GET", endpoint(web.job_id))[1]["choice"] == {"identity": 2}
    code, response = client.call("POST", endpoint(web.job_id), {"none": True})
    assert code == 200 and response["choice"] == {"none": True}
    saved = json.loads(web.choice.read_text())
    assert set(saved) == {"none", "by", "at"} and saved["none"] is True
    assert client.call("GET", endpoint(web.job_id))[1]["choice"] == {"none": True}
    assert client.call("POST", endpoint(web.job_id), {"clear": True})[0] == 200
    assert not web.choice.exists() and job_files(web) == before_files
    assert client.call("GET", endpoint(web.job_id))[1]["choice"] is None
    assert job_snapshot(web) == before_job and web.notes == []


def test_identity_choice_is_published_with_same_directory_atomic_replace(web, monkeypatch):
    previous = json.dumps({"none": True, "by": "admin", "at": "2026-01-01T00:00:00+00:00"})
    web.choice.write_text(previous)
    original_replace, replacements = os.replace, []

    def observe(source, destination, *args, **kwargs):
        if Path(destination) == web.choice:
            source = Path(source)
            assert source != web.choice and source.parent == web.choice.parent
            assert web.choice.read_text() == previous
            assert json.loads(source.read_text())["centre"] == auto_data()["centres"][1]
            replacements.append(source)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", observe)
    assert web.client("admin").call("POST", endpoint(web.job_id), {"identity": 2})[0] == 200
    assert len(replacements) == 1 and not replacements[0].exists()


def test_failed_atomic_replace_preserves_previous_choice_and_removes_temp(web, monkeypatch):
    web.choice.write_text(
        json.dumps({"none": True, "by": "admin", "at": "2026-01-01T00:00:00+00:00"})
    )
    before, original_replace = job_files(web), os.replace

    def cannot_replace(source, destination, *args, **kwargs):
        if Path(destination) == web.choice:
            raise OSError("fixture write failure")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", cannot_replace)
    code, _ = web.client("admin").call("POST", endpoint(web.job_id), {"identity": 2})
    assert code == 500 and job_files(web) == before


def test_choice_reservation_blocks_preopened_worker_until_atomic_choice_is_persisted(
    web, monkeypatch
):
    before = job_snapshot(web)
    ready, attempt_write, first_attempt_done = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    paused, allow_replace, retry_write, worker_done = (threading.Event() for _ in range(4))
    locked, worker_errors, post_errors, post_responses = [], [], [], []
    original_replace = os.replace

    def pause_choice(source, destination, *args, **kwargs):
        if Path(destination) == web.choice:
            paused.set()
            assert allow_replace.wait(5), "test did not release atomic choice commit"
        return original_replace(source, destination, *args, **kwargs)

    def worker():
        database = None
        try:
            # Open before the API starts, just like an already-running worker.
            database = DB(web.root / "reel.db")
            database.conn.execute("PRAGMA busy_timeout=0")
            ready.set()
            assert attempt_write.wait(5)
            try:
                database.set_job(web.job_id, status="running")
            except sqlite3.OperationalError as exc:
                locked.append(str(exc))
            first_attempt_done.set()
            assert retry_write.wait(5)
            assert web.choice.exists(), "worker must proceed only after choice persistence"
            database.set_job(web.job_id, status="running")
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            if database is not None:
                database.conn.close()
            ready.set()
            first_attempt_done.set()
            worker_done.set()

    client = web.client("admin")

    def post_choice():
        try:
            post_responses.append(client.call("POST", endpoint(web.job_id), {"identity": 2}))
        except BaseException as exc:
            post_errors.append(exc)

    monkeypatch.setattr(os, "replace", pause_choice)
    worker_thread = threading.Thread(target=worker, daemon=True)
    post_thread = threading.Thread(target=post_choice, daemon=True)
    worker_thread.start()
    try:
        assert ready.wait(5) and worker_errors == []
        post_thread.start()
        assert paused.wait(5), "API never reached the atomic choice commit"
        assert not web.choice.exists()
        attempt_write.set()
        assert first_attempt_done.wait(5) and worker_errors == []
        assert len(locked) == 1 and "locked" in locked[0].lower(), (
            "worker changed state during the choice reservation"
        )
        assert not web.choice.exists()
        allow_replace.set()
        post_thread.join(5)
        assert not post_thread.is_alive() and post_errors == []
        assert post_responses[0][0] == 200 and web.choice.exists()
        assert job_snapshot(web) == before, "lecturer API changed job/stage records"
        retry_write.set()
        assert worker_done.wait(5) and worker_errors == []
        assert job_snapshot(web)[0]["status"] == "running"
    finally:
        allow_replace.set()
        attempt_write.set()
        retry_write.set()
        worker_thread.join(5)
        if post_thread.ident is not None:
            post_thread.join(5)
        assert not worker_thread.is_alive() and not post_thread.is_alive()


@pytest.mark.parametrize(
    "body",
    [
        {"identity": 0},
        {"identity": -1},
        {"identity": 3},
        {"identity": 7},
        {"identity": "x"},
        {"identity": "2"},
        {"identity": 2.0},
        {"identity": True},
        {"identity": False},
        {"identity": None},
        {"none": False},
        {"clear": False},
        {"clear": "true"},
        {},
        [],
        {"identity": 1, "none": True},
        {"none": True, "clear": True},
        {"identity": 1, "centre": [1] * 128},
    ],
)
def test_invalid_selection_bodies_are_400_and_preserve_existing_choice(web, body):
    web.choice.write_text('{"none":true}')
    before = job_files(web)
    code, response = web.client("admin").call("POST", endpoint(web.job_id), body)
    assert code == 400, response
    assert job_files(web) == before


@pytest.mark.parametrize(
    "vector",
    [
        [0.1] * 127,
        [0.1] * 129,
        [True] * 128,
        ["0.1"] * 128,
        [float("nan")] + [0.1] * 127,
        [float("inf")] * 128,
        [-float("inf")] * 128,
        None,
        {"value": [0.1] * 128},
    ],
)
def test_corrupt_or_nonfinite_identity_centres_are_409_without_overwriting_choice(web, vector):
    data = auto_data()
    data["centres"][1] = vector
    (web.auto_refs / "auto.json").write_text(json.dumps(data))
    web.choice.write_text('{"none":true}')
    before = job_files(web)
    code, response = web.client("admin").call("POST", endpoint(web.job_id), {"identity": 2})
    assert code == 409, response
    assert job_files(web) == before


def test_missing_selected_centre_is_409(web):
    data = auto_data()
    data["centres"] = data["centres"][:1]
    (web.auto_refs / "auto.json").write_text(json.dumps(data))
    assert web.client("admin").call("POST", endpoint(web.job_id), {"identity": 2})[0] == 409
    assert not web.choice.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.jpg",
        "../../lecturer_choice.json",
        "identity_1.png",
        "auto.json",
        "identity_0.jpg",
        "identity_9.jpg",
        "ref_100.jpg",
        "identity_1.jpg/../auto.json",
    ],
)
def test_image_names_and_traversal_are_rejected(web, name):
    code, _ = web.client("admin").call("GET", endpoint(web.job_id, name), csrf=False)
    assert code in (400, 404)


def test_valid_reference_image_is_available_to_admin_without_csrf(web):
    assert web.client("admin").call("GET", endpoint(web.job_id, "ref_12.jpg"), csrf=False) == (
        200,
        JPEG,
    )


def test_image_symlink_cannot_expose_file_outside_auto_refs(web):
    outside = web.root / "private-outside.jpg"
    outside.write_bytes(b"not a face image to expose")
    image = web.auto_refs / "identity_1.jpg"
    image.unlink()
    image.symlink_to(outside)
    code, payload = web.client("admin").call(
        "GET", endpoint(web.job_id, "identity_1.jpg"), csrf=False
    )
    assert code in (400, 404) and payload != outside.read_bytes()


def test_missing_jobs_are_404_even_if_an_orphan_image_directory_exists(web):
    orphan = 999999
    put_auto(web.root, orphan)
    client = web.client("admin")
    assert client.call("GET", endpoint(orphan))[0] == 404
    assert client.call("POST", endpoint(orphan), {"none": True})[0] == 404
    assert client.call("GET", endpoint(orphan, "identity_1.jpg"), csrf=False)[0] == 404
    assert not (web.root / "work" / str(orphan) / "lecturer_choice.json").exists()


@pytest.mark.parametrize(
    "mode,lecturers", [("refs", ["lecturer-one", "lecturer-two"]), ("none", [])]
)
def test_stage_output_fallback_when_discovery_json_is_absent(web, mode, lecturers):
    job_id = new_job(
        web.root,
        status="done",
        blur="done",
        output={
            "lecturer_mode": mode,
            "lecturers": lecturers,
            "lecturer": {"nested": {"centre": [1] * 128}},
        },
    )
    code, response = web.client("admin").call("GET", endpoint(job_id))
    assert code == 200 and response["mode"] == mode and response["lecturers"] == lecturers
    assert (
        response["identities"] == [] and response["refs"] == [] and response["can_choose"] is False
    )


def test_pending_job_has_null_mode_and_discovery_file_takes_priority_over_stage_output(web):
    pending = new_job(web.root, status="queued", blur="pending")
    client = web.client("admin")
    code, response = client.call("GET", endpoint(pending))
    assert code == 200 and response["mode"] is None and response["blur_status"] == "pending"
    assert response["can_choose"] is True
    completed = new_job(
        web.root, status="done", blur="done", output={"lecturer_mode": "refs", "lecturers": ["old"]}
    )
    put_auto(web.root, completed, {**auto_data(), "mode": "chosen", "lecturer_rank": 2})
    code, response = client.call("GET", endpoint(completed))
    assert code == 200 and response["mode"] == "chosen" and response["lecturer_rank"] == 2
    assert response["can_choose"] is False
