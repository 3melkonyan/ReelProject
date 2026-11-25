"""G-18 HTTP regressions with a fully temporary app root and SQLite database.

The production app loads/writes secrets at import time. Execute its source in an
isolated module whose __file__ is already under tmp_path; never import app.py
under its production package name. No worker, media command, or real job runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
import json
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
import uvicorn

from reel.db import DB, SCHEMA


@dataclass
class Reply:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body)


class Client:
    def __init__(self, base: str):
        self.base = base
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, path: str, *, body=None, headers=None) -> Reply:
        values = dict({"X-Requested-With":"reel"} if headers is None else headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            values["Content-Type"] = "application/json"
        request = Request(self.base + path, data=data, headers=values, method=method)
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return Reply(response.code, response.read())


@pytest.fixture
def web(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "reel" / "web" / "app.py"
    fake_file = tmp_path / "reel" / "web" / "app.py"
    (fake_file.parent / "static").mkdir(parents=True)
    (fake_file.parent / "static" / "index.html").write_text("temporary static fixture")
    monkeypatch.setenv("REEL_ADMIN_PASSWORD", "g18-admin-test-password")
    monkeypatch.setenv("REEL_VIEWER_PASSWORD", "g18-viewer-test-password")
    name = f"_reel_g18_web_{uuid4().hex}"
    module = ModuleType(name)
    module.__file__ = str(fake_file)
    monkeypatch.setitem(sys.modules, name, module)
    # From the first executed line, ROOT/STATIC/SECRETS are temporary paths.
    exec(compile(source.read_text(encoding="utf-8"), str(fake_file), "exec"), module.__dict__)
    from account_fixture import existing_admin
    existing_admin(module)
    if module.ROOT != tmp_path or module.SECRETS_FILE != tmp_path / "secrets" / "web.json":
        raise RuntimeError("App root/secrets escaped the temporary fixture")
    if not module.SECRETS_FILE.is_file():
        raise RuntimeError("Temporary app secrets were not initialized")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "WORK", tmp_path / "work")
    monkeypatch.setattr(module, "SECRETS_FILE", tmp_path / "secrets" / "web.json")
    database = tmp_path / "reel.db"
    connections = []

    class RequestDB(DB):
        def __init__(self, path):
            if Path(path).resolve() != database.resolve():
                raise RuntimeError("App attempted non-temporary DB access")
            # Starlette may dispatch separate synchronous calls to different threads.
            self.conn = sqlite3.connect(str(database), isolation_level=None, check_same_thread=False)
            connections.append(self.conn)
            self.conn.row_factory = sqlite3.Row
            # Match DB's deterministic identity helpers on this custom connection.
            from reel.db import email_identity
            self.conn.create_function("casefold", 1, lambda value: value.casefold() if isinstance(value, str) else "", deterministic=True)
            self.conn.create_function("email_identity", 1, email_identity, deterministic=True)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)

    monkeypatch.setattr(module, "DB", RequestDB)
    retries = []
    monkeypatch.setattr(module, "pipeline", SimpleNamespace(retry=lambda db, job: retries.append(job)))
    clock = SimpleNamespace(now=1000.0, sleeps=[])
    # Do not replace time.sleep globally: uvicorn and thread startup need real time.
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock.now, sleep=clock.sleeps.append))
    from reel.web import accounts
    monkeypatch.setattr(accounts,"time",SimpleNamespace(time=lambda:clock.now))
    seed = DB(database)
    job = seed.create_job(str(tmp_path / "source.mp4"), "temporary-g18-job", "Test-Group")
    if job is None:
        raise RuntimeError("Could not create temporary job")
    seed.set_job(job, status="stopped", error="temporary stopped job")
    work = module.WORK / str(job)
    (work / "privacy").mkdir(parents=True)
    face_name = "face_0001.0s_test.jpg"
    face_bytes = b"private-face-fixture"
    (work / "privacy" / face_name).write_bytes(face_bytes)
    (work / "privacy" / "privacy.json").write_text(json.dumps({"frames": 2, "hits": [{"t": 1.0}]}))
    seed.conn.close()

    def mutate(function):
        d = DB(database)
        try:
            return function(d)
        finally:
            d.conn.close()

    def privacy_passed():
        def done(d):
            d.stage_start(job, "privacy")
            d.stage_done(job, "privacy", {"other_faces": 0})
        mutate(done)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(module.app, log_level="critical", access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("Isolated HTTP server did not start")

        def client(role=None):
            result = Client(base)
            if role:
                password = {"admin": "g18-admin-test-password", "viewer": "g18-viewer-test-password"}[role]
                reply = result.request("POST", "/api/login", body={"login":"fixture-admin" if role=="admin" else "", "password": password})
                if reply.status != 200 or reply.json().get("role") != role:
                    raise RuntimeError(f"Fixture {role} login failed with HTTP {reply.status}")
            return result

        yield SimpleNamespace(
            client=client, job=job, work=work, mutate=mutate, passed=privacy_passed,
            retries=retries, clock=clock, face_name=face_name, face_bytes=face_bytes,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Isolated HTTP server failed to stop")
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/me"), ("GET", "/api/summary"), ("GET", "/api/jobs"),
    ("GET", "/api/activity"), ("GET", "/api/groups"),
    ("GET", "/api/jobs/{job}"), ("GET", "/api/jobs/{job}/video"),
    ("GET", "/api/jobs/{job}/thumbnail"),
    ("GET", "/api/jobs/{job}/faces/face_0001.0s_test.jpg"),
    ("POST", "/api/jobs/{job}/retry"), ("POST", "/api/jobs/{job}/resolve"),
])
def test_protected_apis_require_login(web, method, path):
    reply = web.client().request(method, path.format(job=web.job), body={} if method == "POST" else None)
    assert reply.status == 401


def test_logout_requires_login_under_literal_api_policy(web):
    """The literal /api/* policy excludes public login, but includes logout."""
    assert web.client().request("POST", "/api/logout").status == 401


def test_wrong_password_and_five_failure_lockout_expiry(web):
    client = web.client()
    for _ in range(5):
        assert client.request("POST", "/api/login", body={"login":"fixture-admin","password": "wrong-password"}).status == 401
    assert web.clock.sleeps == []
    assert client.request("POST", "/api/login", body={"login":"fixture-admin","password": "wrong-password"}).status == 429
    assert client.request("POST", "/api/login", body={"login":"fixture-admin","password": "g18-admin-test-password"}).status == 429
    web.clock.now += 901
    assert client.request("POST", "/api/login", body={"login":"fixture-admin","password": "g18-admin-test-password"}).status == 200


def test_wrong_unicode_password_is_unauthorized(web):   # bytes compare
    assert web.client().request("POST", "/api/login", body={"password": "սխալ գաղտնաբառ"}).status == 401


@pytest.mark.parametrize("action", ["retry", "resolve"])
def test_admin_mutations_require_requested_with_header(web, action):
    client = web.client("admin")
    body = {"target": "slack", "state": "posted"}
    assert client.request("POST", f"/api/jobs/{web.job}/{action}", body=body, headers={}).status == 400
    assert web.retries == []


@pytest.mark.parametrize("action", ["retry", "resolve"])
def test_viewer_cannot_mutate_jobs(web, action):
    reply = web.client("viewer").request("POST", f"/api/jobs/{web.job}/{action}",
        body={"target": "slack", "state": "posted"}, headers={"X-Requested-With": "reel"})
    assert reply.status == 403
    assert web.retries == []


@pytest.mark.parametrize("action", ["retry", "resolve"])
def test_admin_mutations_positive_control(web, action):
    marker = web.work / "pub_slack.pending"
    marker.write_text("temporary marker")
    reply = web.client("admin").request("POST", f"/api/jobs/{web.job}/{action}",
        body={"target": "slack", "state": "posted"}, headers={"X-Requested-With": "reel"})
    assert reply.status == 200
    if action == "retry":
        assert web.retries == [web.job]
    else:
        assert not marker.exists()
        assert web.mutate(lambda d: d.publication(web.job, "slack")["external_id"]) == "manual"


def test_private_face_crops_are_admin_only(web):
    path = f"/api/jobs/{web.job}/faces/{web.face_name}"
    assert web.client("viewer").request("GET", path).status == 403
    reply = web.client("admin").request("GET", path)
    assert reply.status == 200 and reply.body == web.face_bytes


@pytest.mark.parametrize("name", ["face_..jpg", "%2e%2e", "face_x%2Fsecret.jpg", "thumbnail.jpg"])
def test_admin_face_names_reject_traversal_and_nonface_files(web, name):
    assert web.client("admin").request("GET", f"/api/jobs/{web.job}/faces/{name}").status in {400, 404}


@pytest.mark.parametrize("quality", ["", "?q=full"])
def test_viewer_cannot_watch_video_before_privacy_passes(web, quality):
    (web.work / "preview.mp4").write_bytes(b"preview-fixture")
    (web.work / "final_blurred.mp4").write_bytes(b"blurred-fixture")
    assert web.client("viewer").request("GET", f"/api/jobs/{web.job}/video{quality}").status == 404


@pytest.mark.parametrize("role", ["admin", "viewer"])
@pytest.mark.parametrize("quality", ["", "?q=preview", "?q=full", "?q=raw", "?q=final.mp4"])
def test_raw_final_video_is_never_served(web, role, quality):
    web.passed()
    (web.work / "final.mp4").write_bytes(b"RAW-PRIVATE-VIDEO-MUST-NOT-BE-SERVED")
    assert web.client(role).request("GET", f"/api/jobs/{web.job}/video{quality}").status == 404


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_video_defaults_to_preview_and_full_means_blurred(web, role):
    web.passed()
    (web.work / "final.mp4").write_bytes(b"RAW-PRIVATE-VIDEO")
    (web.work / "preview.mp4").write_bytes(b"PREVIEW-VIDEO")
    (web.work / "final_blurred.mp4").write_bytes(b"FULL-BLURRED-VIDEO")
    client = web.client(role)
    preview = client.request("GET", f"/api/jobs/{web.job}/video")
    full = client.request("GET", f"/api/jobs/{web.job}/video?q=full")
    assert preview.status == full.status == 200
    assert preview.body == b"PREVIEW-VIDEO"
    assert full.body == b"FULL-BLURRED-VIDEO"


def test_viewer_detail_hides_face_list_with_admin_positive_control(web):
    detail = web.client("viewer").request("GET", f"/api/jobs/{web.job}")
    assert detail.status == 200
    assert detail.json()["faces"] == [] and detail.json()["faces_hidden"] is True
    admin = web.client("admin").request("GET", f"/api/jobs/{web.job}").json()
    assert admin["faces_hidden"] is False
    assert [face["name"] for face in admin["faces"]] == [web.face_name]


@pytest.mark.parametrize("target", [
    "youtube",
    "playlist", "slack", "classroom", "library",   # per-publication dry flag
])
def test_each_dry_publication_is_labeled_and_has_no_link(web, target):
    def publications(d):
        if target != "youtube":
            d.add_publication(web.job, "youtube", "real-youtube-id", "https://example.com/real-youtube")
        d.add_publication(web.job, target, f"opaque-dry-{target}", f"https://example.com/fake-{target}")
    web.mutate(publications)
    reply = web.client("viewer").request("GET", f"/api/jobs/{web.job}")
    if reply.status != 200:
        pytest.fail(f"Job detail failed unexpectedly: HTTP {reply.status}")
    publication = reply.json()["publications"][target]
    assert publication["url"] is None and publication.get("dry") is True
