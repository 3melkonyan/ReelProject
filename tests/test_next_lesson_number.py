"""Lesson numbers: the upload page prefill (next_lesson_number, G-29) and the number a
lesson is published with (_lesson_number) follow the same rules."""
from types import SimpleNamespace

from reel import stages as st
from reel.db import DB

CONFIG = {"groups": {"G": {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": ""},
                                                               {"name": "React", "playlist": ""}]},
                     "S": {"slack_channel": "C9", "subjects": [{"name": "Go", "playlist": ""}]}}}


def job(db, path, group="G", subject="Python", status=None, slack=None):
    jid = db.create_job(str(path), f"sha-{path}", group, subject)
    if status:
        db.conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, jid))
    if slack:
        db.add_publication(jid, "slack", slack, None)
    return jid


def test_prefill_counts_announced_and_pending_lessons_of_the_course(tmp_path):
    db = DB(tmp_path / "t.db")
    job(db, tmp_path / "a", slack="ts1", status="done")
    job(db, tmp_path / "b", status="running")
    job(db, tmp_path / "c", status="queued")
    job(db, tmp_path / "d", status="failed")                      # stopped, never announced
    job(db, tmp_path / "e", slack="dry-ts", status="done")        # dry run
    job(db, tmp_path / "f", subject="React", slack="ts2", status="done")
    assert st.next_lesson_number(db, CONFIG, "G", "python") == 4
    assert st.next_lesson_number(db, CONFIG, "G", "React") == 2
    assert st.next_lesson_number(db, CONFIG, "S") == 1


def test_prefill_continues_after_a_named_number(tmp_path):
    db = DB(tmp_path / "t.db")
    job(db, tmp_path / "Դաս 7", group="S", subject=None, slack="ts1", status="done")
    assert st.next_lesson_number(db, CONFIG, "S") == 8


def test_archive_folder_id_is_never_read_as_the_lesson_number(tmp_path):
    db = DB(tmp_path / "t.db")
    jid = job(db, tmp_path / "archive" / "12" / "das-2026-11-18", group="S", subject=None)
    c = SimpleNamespace(db=db, job_id=jid, group="S", config=CONFIG)
    assert st._lesson_number(c, "Go") == 1
    named = job(db, tmp_path / "archive" / "13" / "Դաս 5", group="S", subject=None)
    assert st._lesson_number(SimpleNamespace(db=db, job_id=named, group="S", config=CONFIG), "Go") == 5
