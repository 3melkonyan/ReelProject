"""Phone recording parts on the resumable upload, upload order, current subject.

Real app source under a temporary root (test_web_upload's fixture), fake Slack, no real DB or config.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from reel import pipeline
from reel.db import DB
from reel.web import recording
from reel.web.accounts import create_user
from test_web_upload import send  # noqa: F401 - pytest fixture reused


@pytest.fixture
def copy_remux(monkeypatch):
    calls = []

    def fake(src, dest):
        calls.append((src.name, dest.suffix))
        shutil.copyfile(src, dest)
        return True
    monkeypatch.setattr(recording, "remux", fake)
    return calls


def lecturer(web, groups=("Group-B",), login="lect-b"):
    d = DB(web.root / "reel.db")
    try:
        create_user(d, login, "lecturer-pass-123", {"name": "Դասախոս Բ", "email": f"{login}@example.test",
                                                    "role": "lecturer", "groups": list(groups)})
    finally:
        d.conn.close()
    c = web.client()
    code, r = c.call("POST", "/api/login", body={"login": login, "password": "lecturer-pass-123"})
    assert code == 200 and r["role"] == "lecturer"
    return c


def start(c, **over):
    body = {"group": "Group-B", "subject": "Python", "lesson_number": 3, "mime": "video/webm;codecs=vp8,opus"} | over
    return c.call("POST", "/api/recordings", body=body)


def test_parts_stream_delete_and_confirm_make_one_job(web, copy_remux):
    c = web.client("uploader")
    code, rec = start(c)
    assert code == 200 and rec["lesson"] == "Դաս 3" and rec["parts"] == [] and rec["lesson_date"]
    rid = rec["id"]
    one, two, three = b"first part from the phone", b"off-topic talk to delete", b"third part bytes"
    assert c.call("POST", f"/api/recordings/{rid}/parts")[1]["index"] == 0
    assert send(c, rid, 0, one) == len(one)                        # timeslice chunks, 8 bytes each
    assert c.call("POST", f"/api/recordings/{rid}/parts/0/close", body={"size": len(one), "duration": 61.24})[0] == 200
    assert c.call("POST", f"/api/recordings/{rid}/parts")[1]["index"] == 1
    send(c, rid, 1, two)
    assert c.call("POST", f"/api/recordings/{rid}/parts/1/close", body={"size": len(two)})[0] == 200
    assert c.call("DELETE", f"/api/recordings/{rid}/parts/1")[0] == 200
    assert c.call("PUT", f"/api/uploads/{rid}/1?offset=0", raw=b"late")[0] == 410
    c.call("POST", f"/api/recordings/{rid}/parts")
    send(c, rid, 2, three)
    assert c.call("POST", f"/api/recordings/{rid}/parts/2/close", body={"size": len(three), "interrupted": True})[0] == 200
    code, st = c.call("GET", f"/api/recordings/{rid}")
    assert [p["index"] for p in st["parts"]] == [0, 2] and st["parts"][0]["duration"] == 61.2
    assert st["parts"][1]["interrupted"] is True and all(not p["open"] for p in st["parts"])
    code, done = c.call("POST", f"/api/recordings/{rid}/confirm")
    assert code == 200 and done["job_id"], done
    assert copy_remux == [("part-01.webm", ".mkv"), ("part-03.webm", ".mkv")]
    job = DB(web.root / "reel.db").job(done["job_id"])
    src = Path(job["source_path"])
    assert job["grp"] == "Group-B" and job["subject"] == "Python" and src.name == "Դաս 3"
    assert [p.name for p in pipeline.parts_of(src)] == ["part-01.mkv", "part-02.mkv"]
    assert [p.read_bytes() for p in pipeline.parts_of(src)] == [one, three]
    assert "Հեռախոսով ձայնագրվեց" in web.notes[-1]
    assert c.call("POST", f"/api/recordings/{rid}/confirm") == (200, done)        # idempotent
    assert c.call("GET", f"/api/recordings/{rid}")[1] == {"id": rid, "completed": True, "result": done}


def test_close_needs_every_byte_and_confirm_needs_closed_parts(web, copy_remux):
    c = web.client("uploader")
    rid = start(c, mime="video/mp4;codecs=avc1,mp4a")[1]["id"]
    assert c.call("POST", f"/api/recordings/{rid}/confirm")[0] == 409            # nothing recorded
    c.call("POST", f"/api/recordings/{rid}/parts")
    send(c, rid, 0, b"0123456789")
    code, r = c.call("POST", f"/api/recordings/{rid}/parts/0/close", body={"size": 25})
    assert code == 409 and r == {"received": 10}                                 # phone still has bytes
    assert c.call("POST", f"/api/recordings/{rid}/confirm")[0] == 409            # part 0 still open
    assert c.call("POST", f"/api/recordings/{rid}/parts/0/close", body={"size": 10})[0] == 200
    assert c.call("POST", f"/api/recordings/{rid}/parts/0/close", body={"size": 10})[0] == 200   # retry
    assert c.call("PUT", f"/api/uploads/{rid}/0?offset=10", raw=b"more")[0] == 413  # closed: no growth
    code, done = c.call("POST", f"/api/recordings/{rid}/confirm")
    src = Path(DB(web.root / "reel.db").job(done["job_id"])["source_path"])
    assert [p.name for p in pipeline.parts_of(src)] == ["part-01.mp4"]


def test_empty_part_disappears_and_cancel_removes_everything(web):
    c = web.client("admin")
    rid = start(c)[1]["id"]
    c.call("POST", f"/api/recordings/{rid}/parts")
    assert c.call("POST", f"/api/recordings/{rid}/parts/0/close", body={"size": 0})[0] == 200
    assert c.call("GET", f"/api/recordings/{rid}")[1]["parts"] == []
    assert c.call("DELETE", f"/api/recordings/{rid}")[0] == 200
    assert not (web.root / "out" / "uploads" / rid).exists()
    assert c.call("GET", f"/api/recordings/{rid}")[0] == 404


def test_validation_roles_and_csrf(web):
    c = web.client("uploader")
    assert start(c, subject="Math")[0] == 400
    assert start(c, lesson_number=0)[0] == 400
    assert start(c, mime="video/quicktime")[0] == 400
    assert start(c, group="Nope")[0] == 400
    assert start(web.client("viewer"))[0] == 403
    assert start(web.client())[0] == 401
    assert c.call("POST", "/api/recordings", body={"group": "Group-B"}, csrf=False)[0] == 400


def test_lecturer_records_only_for_own_groups(web, copy_remux):
    other = start(web.client("admin"), group="Group-A", subject="Group-A")[1]["id"]
    lect = lecturer(web)
    assert start(lect, group="Group-A", subject="Group-A")[0] == 404
    assert lect.call("GET", "/api/recordings/defaults?group=Group-A")[0] == 404
    assert lect.call("GET", f"/api/recordings/{other}")[0] == 404
    assert lect.call("POST", f"/api/recordings/{other}/parts")[0] == 404
    assert lect.call("PUT", f"/api/uploads/{other}/0?offset=0", raw=b"x")[0] == 404
    code, rec = start(lect)
    assert code == 200
    lect.call("POST", f"/api/recordings/{rec['id']}/parts")
    send(lect, rec["id"], 0, b"lecturer bytes")
    lect.call("POST", f"/api/recordings/{rec['id']}/parts/0/close", body={"size": 14})
    assert lect.call("POST", f"/api/recordings/{rec['id']}/confirm")[1]["job_id"]


def test_defaults_today_current_subject_and_next_number(web):
    c = web.client("uploader")
    code, d = c.call("GET", "/api/recordings/defaults?group=Group-B")
    assert code == 200 and d["subjects"] == ["Python", "JavaScript"] and d["current"] == "Python"
    assert d["number"] == 1 and d["date"] == recording.lesson_dates.today().isoformat()
    assert c.call("GET", "/api/recordings/info")[1] == {"public_url": None, "max_parts": recording.MAX_PARTS}
    db = DB(web.root / "reel.db")
    db.create_job("x", "sha-js", "Group-B", "JavaScript")
    db.conn.close()
    d = c.call("GET", "/api/recordings/defaults?group=Group-B")[1]
    assert d["current"] == d["subject"] == "JavaScript" and d["number"] == 2     # the last taught subject
    assert c.call("GET", "/api/recordings/defaults?group=Group-B&subject=python")[1]["subject"] == "Python"


def test_current_subject_follows_schedule_periods():
    group = {"subjects": [{"name": "Math", "start": "2026-10-01", "end": "2026-11-15"},
                          {"name": "Python", "period": {"start": "2026-11-16", "end": "2026-12-31"}},
                          {"name": "ML"}]}
    assert recording.current_subject(group, date(2026, 11, 20), "ML") == "Python"
    assert recording.current_subject(group, date(2026, 10, 2), None) == "Math"
    assert recording.current_subject(group, date(2027, 2, 1), "ML") == "ML"          # no period: last lesson
    assert recording.current_subject(group, date(2027, 2, 1), None) == "Math"        # else the first one


def test_ordered_upload_keeps_the_chosen_order(web):
    c = web.client("uploader")
    late, early = b"recorded second", b"recorded first!"
    files = [{"name": "IMG_9.MOV", "size": len(early)}, {"name": "IMG_10.MOV", "size": len(late)}]
    code, r = c.call("POST", "/api/uploads", body={"group": "Group-A", "lesson": "Order", "files": files, "ordered": True})
    assert code == 200 and r["order"] == ["IMG_9.MOV", "IMG_10.MOV"]
    send(c, r["id"], 0, early)
    send(c, r["id"], 1, late)
    done = c.call("POST", f"/api/uploads/{r['id']}/finish")[1]
    src = Path(DB(web.root / "reel.db").job(done["job_id"])["source_path"])
    assert [p.read_bytes() for p in pipeline.parts_of(src)] == [early, late]
    files.reverse()                                  # the user moved IMG_10 up: prefixes keep that order
    r = c.call("POST", "/api/uploads", body={"group": "Group-A", "lesson": "Order 2", "files": files, "ordered": True})[1]
    assert r["order"] == ["IMG_10.MOV", "IMG_9.MOV"]
    send(c, r["id"], 0, b"U" * len(late))
    send(c, r["id"], 1, b"D" * len(early))
    done = c.call("POST", f"/api/uploads/{r['id']}/finish")[1]
    src = Path(DB(web.root / "reel.db").job(done["job_id"])["source_path"])
    assert [p.name for p in pipeline.parts_of(src)] == ["01 IMG_10.MOV", "02 IMG_9.MOV"]


def test_ordered_disk_names():
    assert recording.ordered_disk_names(["a1.mp4", "a2.mp4"]) == ["a1.mp4", "a2.mp4"]
    assert recording.ordered_disk_names(["b.mp4", "a.mp4"]) == ["01 b.mp4", "02 a.mp4"]
    names = recording.ordered_disk_names([f"x{i}.mp4" for i in range(12, 0, -1)])
    assert sorted(names, key=pipeline.natural_key) == names


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg is not installed")
def test_remux_turns_fragmented_mp4_into_a_regular_file(tmp_path):
    src, dest = tmp_path / "part-01.mp4", tmp_path / ".fixed-01.mp4"
    made = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                           "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-c:a", "aac",
                           "-movflags", "frag_keyframe+empty_moov", "-y", str(src)], capture_output=True)
    if made.returncode:
        pytest.skip("this ffmpeg build cannot make the H.264 sample")
    assert recording.remux(src, dest)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dest)],
                           capture_output=True, text=True, check=True)
    assert abs(float(probe.stdout) - 2) < 0.3
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not a video")
    assert not recording.remux(broken, tmp_path / ".fixed-02.mkv") and not (tmp_path / ".fixed-02.mkv").exists()
