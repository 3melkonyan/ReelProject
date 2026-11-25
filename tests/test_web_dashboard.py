"""Exercise the real HTTP application with an isolated DB/config/media root."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from test_web_upload import send

from reel import thumbnail
from reel.db import DB
from reel.web import admin, thumbnail_styles


@pytest.fixture
def isolated(web, monkeypatch):
    monkeypatch.setattr(admin, "ROOT", web.root)
    monkeypatch.setattr(thumbnail_styles, "ROOT", web.root)
    module = next(
        m
        for m in list(sys.modules.values())
        if getattr(m, "__name__", "").startswith("_reel_upload_web_")
        and getattr(m, "ROOT", None) == web.root
    )
    return web, module


def test_next_number_course_scope_and_readonly(isolated):
    web, module = isolated
    c = web.client("uploader")
    query = "/api/uploads/next-number?" + urlencode({"group": "Group-B", "subject": "Python"})
    assert c.call("GET", query)[1] == {"number": 1, "subject": "Python"}
    # accounts exist before any lesson. The lookup must still not mutate lesson data.
    database = DB(web.root / "reel.db")
    assert database.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert database.conn.execute("SELECT count(*) FROM stages").fetchone()[0] == 0
    assert database.conn.execute("SELECT count(*) FROM publications").fetchone()[0] == 0
    database.conn.close()
    db = DB(web.root / "reel.db")
    db.create_job("/inbox/Դաս 5", "one", "Group-B", "Python")
    db.create_job("/inbox/Դաս 12", "two", "Group-B", "JavaScript")
    before = db.conn.total_changes
    assert c.call("GET", query)[1]["number"] == 6
    assert db.conn.total_changes == before
    assert c.call("GET", query, csrf=False)[0] == 400
    assert web.client("viewer").call("GET", query)[0] == 403
    assert web.client().call("GET", query)[0] == 401
    assert c.call("GET", "/api/uploads/next-number?group=Group-B")[0] == 400
    db.conn.close()


def test_thumbnail_privacy_all_roles_and_symlinks(isolated):
    web, module = isolated
    db = DB(web.root / "reel.db")
    jid = db.create_job("/fake/Դաս 1", "thumb", "Group-A")
    folder = module.WORK / str(jid)
    folder.mkdir(parents=True)
    path = folder / "thumbnail.jpg"
    path.write_bytes(b"test-thumbnail")
    url = f"/api/jobs/{jid}/thumbnail"
    for role in ("viewer", "uploader"):
        c = web.client(role)
        assert c.call("GET", url)[0] == 404
        assert c.call("GET", f"/api/jobs/{jid}")[1]["has_thumbnail"] is False
        assert c.call("GET", "/api/jobs")[1][0]["has_thumbnail"] is False
    assert web.client("admin").call("GET", url)[1] == b"test-thumbnail"
    db.stage_start(jid, "privacy")
    db.stage_done(jid, "privacy", {})
    assert web.client("viewer").call("GET", url)[1] == b"test-thumbnail"
    path.unlink()
    outside = web.root / "private.jpg"
    outside.write_bytes(b"private")
    path.symlink_to(outside)
    assert web.client("admin").call("GET", url)[0] == 404
    assert web.client().call("GET", url)[0] == 401
    db.conn.close()


def test_styles_admin_csrf_atomic_and_readonly_catalogue(isolated):
    web, module = isolated
    url = "/api/admin/thumbnail-styles"
    path = web.root / "thumbnail_styles.json"
    c = web.client("admin")
    data = c.call("GET", url)[1]
    assert len(data["styles"]) == len(thumbnail.STYLES)
    assert not path.exists()
    for role in ("viewer", "uploader"):
        other = web.client(role)
        assert other.call("GET", url)[0] == 403
        assert other.call("PUT", url + "/Group-A", body={"style": 3})[0] == 403
        assert other.call("GET", url + "/0/preview.png")[0] == 403
    assert c.call("PUT", url + "/Group-A", body={"style": 3}, csrf=False)[0] == 400
    path.write_text(json.dumps({"retired": 7}))
    assert c.call("PUT", url + "/Group-A", body={"style": 3})[0] == 200
    assert json.loads(path.read_text()) == {"retired": 7, "Group-A": 3}
    assert not list(web.root.glob(".thumbnail_styles-*"))
    before = path.stat().st_mtime_ns
    assert c.call("GET", url)[1]["groups"]["Group-A"] == {"style": 3, "assigned": True}
    assert path.stat().st_mtime_ns == before
    for value in (True, -1, len(thumbnail.STYLES), "1"):
        assert c.call("PUT", url + "/Group-A", body={"style": value})[0] == 400
    assert c.call("PUT", url + "/Unknown", body={"style": 1})[0] == 404
    code, png = c.call("GET", url + "/0/preview.png", csrf=False)
    assert code == 200 and png.startswith(b"\x89PNG")
    assert c.call("GET", url + "/999/preview.png")[0] == 404


def test_resume_receipt_lost_finish_response_and_number(isolated, monkeypatch):
    web, module = isolated
    c = web.client("uploader")
    monkeypatch.setattr(
        module,
        "_upload_video_info",
        lambda parts: {
            "files": [{"width": 1280, "height": 720}],
            "low_resolution": True,
            "resolution_unknown": False,
        },
    )
    body = {
        "group": "Group-B",
        "subject": "Python",
        "lesson_number": 7,
        "files": [{"name": "part.mp4", "size": 12, "last_modified": 123}],
    }
    code, start = c.call("POST", "/api/uploads", body=body)
    assert code == 200
    uid = start["id"]
    send(c, uid, 0, b"abcd")
    assert c.call("GET", f"/api/uploads/{uid}")[1]["received"] == [4]
    assert c.call("GET", f"/api/uploads/{uid}", csrf=False)[0] == 400
    assert web.client("viewer").call("GET", f"/api/uploads/{uid}")[0] == 403
    # A fresh authenticated client resumes the acknowledged bytes.
    c = web.client("uploader")
    send(c, uid, 0, b"abcdefghijkl", start=4)
    code, done = c.call("POST", f"/api/uploads/{uid}/finish")
    assert code == 200
    assert c.call("POST", f"/api/uploads/{uid}/finish")[1] == done
    assert c.call("GET", f"/api/uploads/{uid}")[1] == {"id": uid, "completed": True, "result": done}
    assert c.call("POST", "/api/uploads", body=body)[1]["result"] == done
    assert c.call("DELETE", f"/api/uploads/{uid}")[0] == 409
    db = DB(web.root / "reel.db")
    assert db.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert Path(db.job(done["job_id"])["source_path"]).name == "Դաս 7"
    detail = c.call("GET", f"/api/jobs/{done['job_id']}")[1]
    assert detail["upload_media"]["low_resolution"] is True
    db.conn.close()


@pytest.mark.parametrize(
    "dimensions,low",
    [((1920, 1080), False), ((1080, 1920), False), ((1280, 720), True), ((720, 1280), True)],
)
def test_probe_dimensions(isolated, monkeypatch, dimensions, low):
    web, module = isolated
    calls = []

    def probe(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout=json.dumps(
                {"streams": [dict(zip(("width", "height"), dimensions, strict=False))]}
            )
        )

    monkeypatch.setattr(
        module, "subprocess", SimpleNamespace(run=probe, SubprocessError=RuntimeError)
    )
    result = module._upload_video_info([Path("one.mov"), Path("two.mov")])
    assert len(calls) == 2 and all(a[0] == "ffprobe" for a in calls)
    assert result["low_resolution"] is low and result["resolution_unknown"] is False


def test_probe_failure_is_reported_not_trusted(isolated, monkeypatch):
    web, module = isolated

    def fail(*a, **k):
        raise OSError("no probe")

    monkeypatch.setattr(
        module, "subprocess", SimpleNamespace(run=fail, SubprocessError=RuntimeError)
    )
    assert module._upload_video_info([Path("bad.mov")])["resolution_unknown"] is True


def test_real_phone_video_probe_and_warning(isolated, monkeypatch):
    import shutil
    import subprocess

    web, module = isolated
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = web.root / "phone.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=320x180:rate=1:color=purple",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    content = video.read_bytes()
    monkeypatch.setattr(module, "UPLOAD_CHUNK", len(content))
    c = web.client("uploader")
    body = {
        "group": "Group-B",
        "subject": "Python",
        "lesson_number": 8,
        "files": [{"name": "phone.mov", "size": len(content)}],
    }
    uid = c.call("POST", "/api/uploads", body=body)[1]["id"]
    assert c.call("PUT", f"/api/uploads/{uid}/0?offset=0", raw=content)[0] == 200
    done = c.call("POST", f"/api/uploads/{uid}/finish")[1]
    media = c.call("GET", f"/api/jobs/{done['job_id']}")[1]["upload_media"]
    assert media["low_resolution"] is True and media["resolution_unknown"] is False
    assert (media["files"][0]["width"], media["files"][0]["height"]) == (320, 180)


@pytest.mark.parametrize(
    "payload", [{"streams": None}, {"streams": [{"width": None, "height": 1080}]}]
)
def test_incomplete_probe_metadata_is_unknown(isolated, monkeypatch, payload):
    web, module = isolated
    monkeypatch.setattr(
        module,
        "subprocess",
        SimpleNamespace(
            run=lambda *a, **k: SimpleNamespace(stdout=json.dumps(payload)),
            SubprocessError=RuntimeError,
        ),
    )
    result = module._upload_video_info([Path("odd.mov")])
    assert result["resolution_unknown"] is True
