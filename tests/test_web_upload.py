"""Web upload: uploader role, chunked + resumable transfer, parts -> one queued job, dedup, limits.

Runs the real app source under a temporary root (same isolation idea as test_web.py) with
a real uvicorn server, the real pipeline.enqueue and a temporary SQLite DB.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.cookiejar import CookieJar
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
import uvicorn

from reel import pipeline
from reel.db import DB

PW = {"admin": "up-admin-pw", "viewer": "up-viewer-pw", "uploader": "up-uploader-pw"}


class Client:
    def __init__(self, base):
        self.base, self.opener = (
            base,
            build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar())),
        )

    def call(self, method, path, body=None, raw=None, csrf=True):
        headers = {"X-Requested-With": "reel"} if csrf else {}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        try:
            r = self.opener.open(
                Request(self.base + path, data=data, headers=headers, method=method), timeout=10
            )
        except HTTPError as e:
            r = e
        with r:
            text = r.read()
        return r.code, (json.loads(text) if text[:1] in (b"{", b"[") else text)


@pytest.fixture
def web(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "reel" / "web" / "app.py"
    fake = tmp_path / "reel" / "web" / "app.py"
    (fake.parent / "static").mkdir(parents=True)
    (fake.parent / "static" / "index.html").write_text("fixture")
    (tmp_path / "config.yaml").write_text(
        'groups:\n  Group-A:\n    playlist_id: "PL1"\n    slack_channel: "C1"\n    classroom_course_id: "1"\n'
        '    lecturers: [l1]\n  Group-B:\n    slack_channel: "C2"\n    subjects:\n'
        "    - {name: Python, playlist: PLpy}\n    - {name: JavaScript, playlist: PLjs}\n"
        'operator_slack_channel: "C1"\ncut:\n  min_silence_sec: 120\n  max_cut_ratio: 0.4\n',
        encoding="utf-8",
    )
    for r, pw in PW.items():
        monkeypatch.setenv(f"REEL_{r.upper()}_PASSWORD", pw)
    module = ModuleType(f"_reel_upload_web_{uuid4().hex}")
    module.__file__ = str(fake)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source.read_text(encoding="utf-8"), str(fake), "exec"), module.__dict__)
    from account_fixture import existing_admin

    existing_admin(module)
    assert tmp_path == module.ROOT
    monkeypatch.setattr(module, "UPLOAD_CHUNK", 8)  # tiny chunks: exercise many PUTs
    notes = []
    monkeypatch.setattr(pipeline, "_notify", notes.append)  # never reach Slack from a test
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(
        uvicorn.Config(module.app, log_level="critical", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"

    def client(role=None):
        c = Client(base)
        if role:
            code, r = c.call(
                "POST",
                "/api/login",
                body={"login": "fixture-admin" if role == "admin" else "", "password": PW[role]},
            )
            assert code == 200 and r["role"] == role
        return c

    yield type(
        "W",
        (),
        {
            "client": staticmethod(client),
            "root": tmp_path,
            "notes": notes,
            "secrets": module.SECRETS_FILE,
            "module": module,
        },
    )
    server.should_exit = True
    thread.join(5)


def send(c, uid, index, data, chunk=8, start=0):
    off = start
    while off < len(data):
        code, r = c.call(
            "PUT", f"/api/uploads/{uid}/{index}?offset={off}", raw=data[off : off + chunk]
        )
        assert code in (200, 409), (code, r)
        off = r["received"]
    return off


def test_uploader_sends_two_parts_and_gets_one_queued_job(web):
    c = web.client("uploader")
    one, two = b"first part bytes!", b"second part, a bit longer"
    files = [{"name": "das_2.mp4", "size": len(two)}, {"name": "das_1.mp4", "size": len(one)}]
    code, r = c.call(
        "POST", "/api/uploads", body={"group": "Group-A", "lesson": "Das 18.11", "files": files}
    )
    assert (
        code == 200
        and r["lesson"] == "Das 18.11"
        and r["order"] == ["das_1.mp4", "das_2.mp4"]
        and r["received"] == [0, 0]
    )
    assert send(c, r["id"], 1, one) == len(one)
    half = send(c, r["id"], 0, two[:8])  # connection "drops" after 8 bytes
    code, again = c.call(
        "POST", "/api/uploads", body={"group": "Group-A", "lesson": "Das 18.11", "files": files}
    )
    assert again["id"] == r["id"] and again["received"] == [half, len(one)]  # resume, not restart
    assert send(c, r["id"], 0, two, start=half) == len(two)
    code, done = c.call("POST", f"/api/uploads/{r['id']}/finish")
    assert code == 200 and done["job_id"]
    job = DB(web.root / "reel.db").job(done["job_id"])
    src = Path(job["source_path"])
    assert job["status"] == "queued" and job["grp"] == "Group-A" and src.name == "Das 18.11"
    assert [p.read_bytes() for p in pipeline.parts_of(src)] == [one, two]
    assert not (web.root / "out" / "uploads" / r["id"]).exists()
    assert "Das 18.11" in web.notes[-1]
    # the very same parts again -> duplicate, no second job
    code, r2 = c.call(
        "POST", "/api/uploads", body={"group": "Group-A", "lesson": "copy", "files": files}
    )
    send(c, r2["id"], 0, two)
    send(c, r2["id"], 1, one)
    code, dup = c.call("POST", f"/api/uploads/{r2['id']}/finish")
    assert code == 200 and dup == {"duplicate_of": done["job_id"]}


def test_roles_and_csrf(web):
    body = {"group": "Group-A", "lesson": "x", "files": [{"name": "a.mp4", "size": 3}]}
    assert web.client("viewer").call("POST", "/api/uploads", body=body)[0] == 403
    assert web.client().call("POST", "/api/uploads", body=body)[0] == 401
    assert web.client("uploader").call("POST", "/api/uploads", body=body, csrf=False)[0] == 400
    assert web.client("admin").call("POST", "/api/uploads", body=body)[0] == 200
    up = web.client("uploader")
    assert up.call("POST", "/api/jobs/1/retry")[0] == 403  # no admin actions
    assert "uploader_password" in json.loads(web.secrets.read_text())  # generated for old installs


def test_rejects_bad_input_and_oversized_chunks(web):
    c = web.client("uploader")

    def start(files, group="Group-A"):
        return c.call("POST", "/api/uploads", body={"group": group, "lesson": "x", "files": files})

    assert start([{"name": "a.mp4", "size": 3}], group="Nope")[0] == 400
    assert start([{"name": "notes.txt", "size": 3}])[0] == 400
    assert start([{"name": "a.mp4", "size": 0}])[0] == 400
    assert start([{"name": "a.mp4", "size": 3}, {"name": "A.mp4", "size": 3}])[0] == 400
    code, r0 = c.call(
        "POST",
        "/api/uploads",
        body={
            "group": "Group-A",
            "lesson": "Das (1/2) <b>",
            "files": [{"name": "x.mp4", "size": 1}],
        },
    )
    assert r0["lesson"] == "Das (1_2) _b"  # no slashes or markup
    code, r = start([{"name": "../../evil.mp4", "size": 4}])
    assert code == 200 and r["order"] == ["evil.mp4"]  # path stripped, stays inside
    assert (
        c.call("PUT", f"/api/uploads/{r['id']}/0?offset=0", raw=b"12345")[0] == 413
    )  # > declared size
    assert c.call("PUT", f"/api/uploads/{r['id']}/0?offset=0", raw=b"1234")[0] == 200
    assert c.call("PUT", "/api/uploads/zzzz/0?offset=0", raw=b"1")[0] == 404
    code, r2 = start([{"name": "b.mp4", "size": 5}])
    assert c.call("POST", f"/api/uploads/{r2['id']}/finish")[0] == 409  # not complete
    assert c.call("DELETE", f"/api/uploads/{r2['id']}")[0] == 200
    assert not (web.root / "out" / "uploads" / r2["id"]).exists()


def test_subject_is_required_for_a_group_with_several_and_stored_on_the_job(web):
    c = web.client("uploader")
    files = [{"name": "a.mp4", "size": 3}]
    code, r = c.call(
        "POST", "/api/uploads", body={"group": "Group-B", "lesson": "L", "files": files}
    )
    assert code == 400 and "subject" in r["detail"]
    assert (
        c.call(
            "POST",
            "/api/uploads",
            body={"group": "Group-B", "subject": "Go", "lesson": "L", "files": files},
        )[0]
        == 400
    )
    code, r = c.call(
        "POST",
        "/api/uploads",
        body={"group": "Group-B", "subject": "javascript", "lesson": "L", "files": files},
    )
    assert code == 200 and r["subject"] == "JavaScript"
    send(c, r["id"], 0, b"abc")
    code, done = c.call("POST", f"/api/uploads/{r['id']}/finish")
    assert DB(web.root / "reel.db").job(done["job_id"])["subject"] == "JavaScript"
    code, r1 = c.call(
        "POST", "/api/uploads", body={"group": "Group-A", "lesson": "L1", "files": files}
    )
    assert code == 200 and r1["subject"] == "Group-A"  # one subject: implicit


def test_admin_sets_the_subject_of_a_lesson_that_is_not_published_yet(web):
    d = DB(web.root / "reel.db")
    jid = d.create_job(str(web.root / "x"), "sha-subject", "Group-B")
    admin = web.client("admin")
    assert admin.call("POST", f"/api/jobs/{jid}/subject", body={"subject": "Go"})[0] == 400
    code, r = admin.call("POST", f"/api/jobs/{jid}/subject", body={"subject": "python"})
    assert code == 200 and d.job(jid)["subject"] == "Python"
    assert (
        web.client("uploader").call("POST", f"/api/jobs/{jid}/subject", body={"subject": "Python"})[
            0
        ]
        == 403
    )
    d.set_job(jid, status="done")
    assert admin.call("POST", f"/api/jobs/{jid}/subject", body={"subject": "JavaScript"})[0] == 409


def test_finish_refuses_a_group_removed_during_the_upload(web):
    """Settings can change while a long upload runs; finish re-checks the group."""
    c = web.client("uploader")
    code, r = c.call(
        "POST",
        "/api/uploads",
        body={
            "group": "Group-B",
            "subject": "Python",
            "lesson": "L",
            "files": [{"name": "a.mp4", "size": 3}],
        },
    )
    send(c, r["id"], 0, b"abc")
    cfg = web.root / "config.yaml"
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text(text.replace("Group-B:", "Group-C:"), encoding="utf-8")  # renamed meanwhile
    code, res = c.call("POST", f"/api/uploads/{r['id']}/finish")
    assert code == 409 and res["detail"] == "group removed during upload"
    assert (web.root / "out" / "uploads" / r["id"]).exists()  # nothing lost: cancel or fix
    cfg.write_text(text, encoding="utf-8")
    code, done = c.call("POST", f"/api/uploads/{r['id']}/finish")
    assert code == 200 and done["job_id"]
