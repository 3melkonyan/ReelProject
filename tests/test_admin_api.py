"""Admin HTTP integration tests; every write stays in an isolated temporary ROOT.

The app source is executed with a temporary __file__, and served on an ephemeral
localhost port. No production app import, worker, credentials or live API is used.
"""
from __future__ import annotations

import copy
import json
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
import uvicorn

from reel import config as config_module, pipeline
from reel.db import DB
from reel.publish import youtube
from reel.publish.errors import PublishError
from reel.web import admin

PROJECT = Path(__file__).resolve().parents[1]
PASSWORDS = {"admin": "admin-settings-fixture", "viewer": "viewer-settings-fixture",
             "uploader": "uploader-settings-fixture"}
PLAYLISTS = [{"id": "PLpy", "title": "Python դասեր", "privacy": "unlisted", "count": 12},
             {"id": "PLjs", "title": "JavaScript", "privacy": "private", "count": 3}]


def group_body(**changes):
    body = {"subjects": [{"name": "Python", "playlist": "PLpy"},
                         {"name": "JavaScript", "playlist": "PLjs"}],
            "slack_channel": "Cgroup", "lecturers": []}
    body.update(changes)
    return body


def settings_body(**changes):
    body = {"operator_slack_channel": "Coperator-new",
            "cut": {"min_silence_sec": 60, "max_cut_ratio": 0.25}}
    body.update(changes)
    return body


def group_url(name):
    return "/api/admin/groups/" + quote(name, safe="")


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, *, csrf=True):
        headers = {"X-Requested-With": "reel" if csrf is True else csrf} if csrf else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            response = self.opener.open(Request(self.base + path, data=data, headers=headers,
                                                method=method), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
        return response.code, json.loads(raw) if raw[:1] in (b"{", b"[") else raw


@pytest.fixture(scope="module", autouse=True)
def production_config_is_untouched():
    path = PROJECT / "config.yaml"
    before = path.stat().st_mtime_ns if path.exists() else None
    yield
    after = path.stat().st_mtime_ns if path.exists() else None
    assert after == before, "admin integration tests changed the REAL config.yaml mtime"


@pytest.fixture
def web(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    initial = {"ui_language": "hy", "groups": {"Group-A": group_body(classroom_course_id="123"),
                           "Group-B": group_body(subjects=[{"name": "C++", "playlist": "PLcpp"}],
                                                  slack_channel="Cother", lecturers=["lecturer1"])},
               "operator_slack_channel": "Coperator",
               "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4, "pad_sec": 1.5}}
    config_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")
    refs = tmp_path / "refs" / "lecturer1"
    refs.mkdir(parents=True)
    (refs / "one.jpg").write_bytes(b"fixture-photo-1")
    (refs / "two.jpg").write_bytes(b"fixture-photo-2")
    (refs / "notes.txt").write_text("not a reference photo")
    (tmp_path / "refs" / "not-a-directory.jpg").write_bytes(b"not a directory")

    # Fail before opening any nonfixture config/DB, including accidental defaults.
    original_resolve, original_db_init = config_module._resolve, DB.__init__

    def isolated_config(path):
        target = original_resolve(path)
        assert target.resolve().is_relative_to(tmp_path), f"nonfixture config access: {target}"
        return target

    def isolated_db(self, path="reel.db"):
        target = Path(path).resolve()
        assert target == tmp_path / "reel.db", f"nonfixture DB access: {target}"
        original_db_init(self, target)

    monkeypatch.setattr(config_module, "_resolve", isolated_config)
    monkeypatch.setattr(DB, "__init__", isolated_db)
    monkeypatch.setattr(admin, "ROOT", tmp_path, raising=False)
    notes, playlist_calls = [], []
    monkeypatch.setattr(pipeline, "_notify", notes.append)

    def playlists():
        playlist_calls.append(True)
        return copy.deepcopy(PLAYLISTS)

    def no_live_service(*args, **kwargs):
        raise AssertionError("admin tests must not create a live YouTube service")

    monkeypatch.setattr(youtube, "list_playlists", playlists, raising=False)
    monkeypatch.setattr(youtube, "_service", no_live_service)
    for role, password in PASSWORDS.items():
        monkeypatch.setenv(f"REEL_{role.upper()}_PASSWORD", password)

    source = PROJECT / "reel" / "web" / "app.py"
    fake = tmp_path / "reel" / "web" / "app.py"
    (fake.parent / "static").mkdir(parents=True)
    (fake.parent / "static" / "index.html").write_text("admin test fixture")
    module = ModuleType(f"_reel_admin_web_{uuid4().hex}")
    module.__file__ = str(fake)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source.read_text(encoding="utf-8"), str(fake), "exec"), module.__dict__)
    from account_fixture import existing_admin
    existing_admin(module)
    assert module.ROOT == admin.ROOT == tmp_path
    database = DB(tmp_path / "reel.db")
    database.conn.close()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(uvicorn.Config(module.app, log_level="critical", access_log=False,
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "isolated admin test server did not start"
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"

        def client(role=None):
            result = Client(base)
            if role:
                code, body = result.call("POST", "/api/login", {"login":"fixture-admin" if role=="admin" else "", "password": PASSWORDS[role]})
                assert code == 200 and body["role"] == role
            return result

        yield SimpleNamespace(root=tmp_path, config_path=config_path, client=client,
                              playlist_calls=playlist_calls, notes=notes)
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive(), "isolated admin test server did not stop"


def saved(web):
    return config_module.load_config(web.config_path)


def snapshot(web):
    path = web.config_path
    backup = path.with_name(path.name + ".bak")
    return path.read_bytes(), path.stat().st_mtime_ns, backup.read_bytes() if backup.exists() else None


def add_job(web, *, status="queued", group="Group-A", subject="Python"):
    database = DB(web.root / "reel.db")
    try:
        job_id = database.create_job(str(web.root / "archive" / "fixture.mp4"), uuid4().hex, group, subject)
        database.set_job(job_id, status=status)
        return job_id
    finally:
        database.conn.close()


def test_all_admin_routes_reject_viewer_uploader_and_anonymous(web):
    routes = [("GET", "/api/admin/config", None), ("PUT", group_url("Group-A"), group_body()),
              ("DELETE", group_url("Group-A"), None),
              ("PUT", "/api/admin/settings", settings_body()),
              ("GET", "/api/admin/youtube/playlists", None)]
    before = snapshot(web)
    for role in ("viewer", "uploader", None):
        client = web.client(role)
        for method, path, body in routes:
            code, response = client.call(method, path, body)
            assert code == 403 if role else code in (401, 403), (role, path, code, response)
    assert snapshot(web) == before and web.playlist_calls == []


@pytest.mark.parametrize("csrf", [False, "XMLHttpRequest"])
def test_every_admin_route_requires_exact_csrf_header(web, csrf):
    client = web.client("admin")
    before = snapshot(web)
    for method, path, body in [("GET", "/api/admin/config", None),
                               ("PUT", group_url("Group-A"), group_body()),
                               ("DELETE", group_url("Group-A"), None),
                               ("PUT", "/api/admin/settings", settings_body()),
                               ("GET", "/api/admin/youtube/playlists", None)]:
        code, response = client.call(method, path, body, csrf=csrf)
        assert code == 400, (path, code, response)
    assert snapshot(web) == before and web.playlist_calls == []


def test_config_returns_normalized_groups_and_reference_counts_without_writing(web):
    before = snapshot(web)
    code, response = web.client("admin").call("GET", "/api/admin/config")
    assert code == 200
    assert response["config"] == saved(web)
    assert response["refs"]["lecturer1"] == 2 and "not-a-directory.jpg" not in response["refs"]
    assert snapshot(web) == before


@pytest.mark.parametrize("framing", ["board", "off", "auto", "projector"])
def test_group_framing_accepts_explicit_board_and_off(web, framing):
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(framing=framing))
    assert code == 200, response
    assert saved(web)["groups"]["Group-A"]["framing"] == framing
    assert response["config"]["groups"]["Group-A"]["framing"] == framing


@pytest.mark.parametrize("framing", [None, True, False, 0, 1, "", "BOARD", " board ", "automatic", [], {}])
def test_invalid_framing_returns_armenian_400_and_preserves_config_and_backup(web, framing):
    client = web.client("admin")
    assert client.call("PUT", group_url("Group-A"), group_body(framing="board"))[0] == 200
    before = snapshot(web)
    code, response = client.call("PUT", group_url("Group-A"), group_body(framing=framing))
    assert code == 400 and "Կադրի ռեժիմը" in response["detail"], response
    assert snapshot(web) == before


@pytest.mark.parametrize("framing", ["board", "off", "auto", "projector"])
def test_omitted_framing_keeps_the_existing_group_setting(web, framing):
    client = web.client("admin")
    assert client.call("PUT", group_url("Group-A"), group_body(framing=framing))[0] == 200
    code, response = client.call("PUT", group_url("Group-A"), group_body(slack_channel="Cchanged"))
    assert code == 200, response
    group = saved(web)["groups"]["Group-A"]
    assert group["framing"] == framing and group["slack_channel"] == "Cchanged"


def test_new_group_without_framing_defaults_to_full_frame(web):
    code, response = web.client("admin").call("PUT", group_url("New-Group"), group_body())
    assert code == 200, response
    assert saved(web)["groups"]["New-Group"].get("framing", "off") == "off"


def test_creates_unicode_group_with_two_subjects_normalizes_urls_and_keeps_backup(web):
    before = web.config_path.read_bytes()
    body = group_body(subjects=[{"name": " Python ", "playlist": "https://www.youtube.com/playlist?list=PLnew-py&index=2"},
                                {"name": "JavaScript", "playlist": "https://www.youtube.com/watch?v=video&list=PLnew-js"}],
                      lecturers=["lecturer1"], classroom_course_id="")
    code, response = web.client("admin").call("PUT", group_url("Խումբ 3"), body)
    assert code == 200, response
    group = saved(web)["groups"]["Խումբ 3"]
    assert group["subjects"] == [{"name": "Python", "playlist": "PLnew-py"}, {"name": "JavaScript", "playlist": "PLnew-js"}]
    assert group["lecturers"] == ["lecturer1"] and "classroom_course_id" not in group
    assert web.config_path.with_name("config.yaml.bak").read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"subjects": []},
    {"subjects": [{"name": f"Subject{i}", "playlist": f"PL{i}"} for i in range(6)]},
    {"subjects": [{"name": "Python", "playlist": "PL1"}, {"name": "python", "playlist": "PL2"}]},
    {"subjects": [{"name": "Python", "playlist": "PL1"}, {"name": "Go", "playlist": "PL1"}]},
    {"subjects": [{"name": "Python", "playlist": "PLsame"}, {"name": "Go", "playlist": "https://www.youtube.com/playlist?list=PLsame"}]},
    {"subjects": [{"name": "x" * 41, "playlist": "PL1"}]},
    {"slack_channel": ""}, {"lecturers": ["../outside"]}, {"lecturers": ["missing-ref"]},
    {"classroom_course_id": 123},
])
def test_invalid_groups_return_400_without_touching_config_or_backup(web, changes):
    before = snapshot(web)
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(**changes))
    assert code == 400 and response.get("detail"), response
    assert snapshot(web) == before


@pytest.mark.parametrize("name", ["../x", "..", "bad/name", "x" * 61])
def test_invalid_group_paths_are_rejected_without_filesystem_or_config_changes(web, name):
    before = snapshot(web)
    code, response = web.client("admin").call("PUT", group_url(name), group_body())
    assert code == 400, response
    assert snapshot(web) == before and not (web.root / "x").exists()


@pytest.mark.parametrize("status", ["queued", "running", "stopped"])
def test_active_subject_cannot_be_removed_or_renamed_and_conflict_names_job_ids(web, status):
    job_id = add_job(web, status=status)
    client, before = web.client("admin"), snapshot(web)
    for subjects in ([{"name": "JavaScript", "playlist": "PLjs"}],
                     [{"name": "Go", "playlist": "PLpy"}, {"name": "JavaScript", "playlist": "PLjs"}]):
        code, response = client.call("PUT", group_url("Group-A"), group_body(subjects=subjects))
        assert code == 409 and str(job_id) in json.dumps(response), response
        assert snapshot(web) == before


def test_subject_conflicts_include_jobs_older_than_default_db_page(web):
    old = add_job(web)
    for _ in range(55):
        add_job(web, status="done", group="Group-B", subject="C++")
    code, response = web.client("admin").call("PUT", group_url("Group-A"),
                                              group_body(subjects=[{"name": "JavaScript", "playlist": "PLjs"}]))
    assert code == 409 and str(old) in json.dumps(response), response


@pytest.mark.parametrize("subject", [None, ""])
def test_destructive_subject_edits_block_queued_jobs_without_a_resolved_subject(web, subject):
    client = web.client("admin")
    original = group_body(subjects=[{"name": "Python", "playlist": "PLpy"}])
    assert client.call("PUT", group_url("Group-A"), original)[0] == 200
    job_id = add_job(web, subject=subject)
    before = snapshot(web)
    replacement = group_body(subjects=[{"name": "Go", "playlist": "PLgo"}])
    code, response = client.call("PUT", group_url("Group-A"), replacement)
    assert code == 409 and str(job_id) in json.dumps(response), response
    assert snapshot(web) == before and saved(web)["groups"]["Group-A"] == original


def test_completed_subject_jobs_do_not_block_edits_and_history_is_preserved(web):
    job_id = add_job(web, status="done")
    code, response = web.client("admin").call("PUT", group_url("Group-A"),
                                              group_body(subjects=[{"name": "JavaScript", "playlist": "PLjs"}]))
    assert code == 200, response
    assert len(saved(web)["groups"]["Group-A"]["subjects"]) == 1
    database = DB(web.root / "reel.db")
    try:
        assert database.job(job_id)["subject"] == "Python" and database.job(job_id)["status"] == "done"
    finally:
        database.conn.close()


def test_unchanged_active_subject_and_other_groups_do_not_block_safe_update(web):
    add_job(web, subject="JavaScript")
    code, response = web.client("admin").call("PUT", group_url("Group-A"),
                                              group_body(subjects=[{"name": "JavaScript", "playlist": "PLjs"}], slack_channel="Cupdated"))
    assert code == 200, response
    assert saved(web)["groups"]["Group-A"]["slack_channel"] == "Cupdated"


@pytest.mark.parametrize("status", ["queued", "running", "stopped"])
def test_delete_rejects_any_noncompleted_group_job(web, status):
    job_id = add_job(web, status=status)
    before = snapshot(web)
    code, response = web.client("admin").call("DELETE", group_url("Group-A"))
    assert code == 409 and str(job_id) in json.dumps(response), response
    assert snapshot(web) == before


@pytest.mark.parametrize("relative", ["lesson.mp4", "Lesson/part1.mp4", ".uploading"])
def test_delete_rejects_inbox_files_including_lesson_parts(web, relative):
    path = web.root / "inbox" / "Group-A" / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unprocessed fixture")
    before = snapshot(web)
    code, response = web.client("admin").call("DELETE", group_url("Group-A"))
    assert code == 409, response
    assert snapshot(web) == before and path.read_bytes() == b"unprocessed fixture"


def test_delete_allows_done_history_but_never_deletes_the_last_group(web):
    job_id = add_job(web, status="done", group="Group-B", subject="C++")
    client = web.client("admin")
    assert client.call("DELETE", group_url("Group-B"))[0] == 200
    assert set(saved(web)["groups"]) == {"Group-A"}
    before = snapshot(web)
    assert client.call("DELETE", group_url("Group-A"))[0] == 409
    assert snapshot(web) == before
    database = DB(web.root / "reel.db")
    try:
        assert database.job(job_id)["grp"] == "Group-B"
    finally:
        database.conn.close()


def test_rename_moves_the_entire_inbox_directory_without_changing_file_bytes(web):
    source = web.root / "inbox" / "Group-A" / "Lesson" / "part.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"inbox original bytes")
    old = saved(web)["groups"]["Group-A"]
    code, response = web.client("admin").call("PUT", group_url("Group-A"), {**old, "rename_to": "Նոր խումբ"})
    assert code == 200, response
    assert "Group-A" not in saved(web)["groups"] and saved(web)["groups"]["Նոր խումբ"] == old
    assert not (web.root / "inbox" / "Group-A").exists()
    assert (web.root / "inbox" / "Նոր խումբ" / "Lesson" / "part.mp4").read_bytes() == b"inbox original bytes"


@pytest.mark.parametrize("target", ["Group-B", "ExistingInbox", "../outside"])
def test_rename_never_overwrites_an_existing_group_inbox_or_escapes_root(web, target):
    source = web.root / "inbox" / "Group-A" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    existing = web.root / "inbox" / "ExistingInbox" / "keep.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"destination")
    before = snapshot(web)
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(rename_to=target))
    assert code in ((400,) if target.startswith("..") else (400, 409)), response
    assert snapshot(web) == before and source.read_bytes() == b"source" and existing.read_bytes() == b"destination"


@pytest.mark.parametrize("status", ["queued", "running", "stopped"])
def test_rename_blocks_noncompleted_jobs_even_when_subjects_are_unchanged(web, status):
    job_id = add_job(web, status=status)
    source = web.root / "inbox" / "Group-A" / "part.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pending lesson")
    before = snapshot(web)
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(rename_to="Renamed"))
    assert code == 409 and str(job_id) in json.dumps(response), response
    assert snapshot(web) == before and source.read_bytes() == b"pending lesson"
    assert not (web.root / "inbox" / "Renamed").exists()


def test_rename_rolls_back_inbox_move_if_atomic_config_save_fails(web, monkeypatch):
    source = web.root / "inbox" / "Group-A" / "Lesson" / "part.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original lesson bytes")
    destination = web.root / "inbox" / "Renamed" / "Lesson" / "part.mp4"
    web.config_path.with_name("config.yaml.bak").write_bytes(b"previous backup fixture")
    before = snapshot(web)
    attempts = []

    def cannot_save(config, path):
        attempts.append(Path(path))
        assert destination.read_bytes() == b"original lesson bytes" and not source.exists()
        raise OSError("fixture disk failure")

    monkeypatch.setattr(admin, "save_config", cannot_save)
    code, response = web.client("admin").call("PUT", group_url("Group-A"), group_body(rename_to="Renamed"))
    assert code == 500, response
    assert attempts == [web.config_path] and snapshot(web) == before
    assert source.read_bytes() == b"original lesson bytes"
    assert not (web.root / "inbox" / "Renamed").exists()


def test_concurrent_group_and_settings_updates_both_survive_read_modify_save(web, monkeypatch):
    original_load = admin.load_config

    def slow_read(path):
        result = original_load(path)
        time.sleep(0.05)  # Without the shared lock both writers read the same old config.
        return result

    monkeypatch.setattr(admin, "load_config", slow_read)
    group_client, settings_client = web.client("admin"), web.client("admin")
    with ThreadPoolExecutor(max_workers=2) as pool:
        group_request = pool.submit(group_client.call, "PUT", group_url("Parallel"), group_body())
        settings_request = pool.submit(settings_client.call, "PUT", "/api/admin/settings", settings_body())
        assert group_request.result(timeout=10)[0] == 200
        assert settings_request.result(timeout=10)[0] == 200
    result = saved(web)
    assert "Parallel" in result["groups"] and len(result["groups"]) == 3
    assert result["operator_slack_channel"] == "Coperator-new"
    assert result["cut"] == {"min_silence_sec": 60, "max_cut_ratio": 0.25, "pad_sec": 1.5}


def test_settings_update_preserves_groups_and_other_cut_values_and_creates_backup(web):
    before_bytes, before_config = web.config_path.read_bytes(), saved(web)
    code, response = web.client("admin").call("PUT", "/api/admin/settings", settings_body())
    assert code == 200, response
    result = saved(web)
    assert result["groups"] == before_config["groups"]
    assert result["operator_slack_channel"] == "Coperator-new"
    assert result["cut"] == {"min_silence_sec": 60, "max_cut_ratio": 0.25, "pad_sec": 1.5}
    assert web.config_path.with_name("config.yaml.bak").read_bytes() == before_bytes


@pytest.mark.parametrize("field,value", [("min_silence_sec", 0), ("min_silence_sec", -1),
                                          ("min_silence_sec", True), ("max_cut_ratio", 0),
                                          ("max_cut_ratio", 1), ("max_cut_ratio", float("nan")),
                                          ("max_cut_ratio", "bad")])
def test_invalid_settings_do_not_change_the_config_or_backup(web, field, value):
    body = settings_body()
    body["cut"][field] = value
    before = snapshot(web)
    code, response = web.client("admin").call("PUT", "/api/admin/settings", body)
    assert code == 400 and response.get("detail"), response
    assert snapshot(web) == before


def test_playlists_endpoint_uses_mock_and_returns_the_contract_fields_without_writes(web):
    before = snapshot(web)
    code, response = web.client("admin").call("GET", "/api/admin/youtube/playlists")
    assert code == 200 and response == PLAYLISTS
    assert web.playlist_calls == [True] and snapshot(web) == before


def test_playlist_publish_error_becomes_502_with_message(web, monkeypatch):
    def unavailable():
        raise PublishError("fixture YouTube authorization unavailable", retryable=False)
    monkeypatch.setattr(youtube, "list_playlists", unavailable)
    before = snapshot(web)
    code, response = web.client("admin").call("GET", "/api/admin/youtube/playlists")
    assert code == 502 and "fixture YouTube authorization unavailable" in json.dumps(response), response
    assert snapshot(web) == before


def test_group_editor_accepts_existing_processing_and_correction_options(web):
    body=group_body(framing='auto')
    body.update(framing_correction='perspective',processing={'studio_audio':{'enabled':True}})
    code,result=web.client('admin').call('PUT',group_url('Group-A'),body)
    assert code==200,result
    group=saved(web)['groups']['Group-A']
    assert group['framing']=='auto' and group['framing_correction']=='perspective'
    assert group['processing']==body['processing']
