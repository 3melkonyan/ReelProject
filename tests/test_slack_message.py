"""Owner, 2026-11-19: the Slack line is "Python դասընթացի 5-րդ դասի տեսանյութը տեղադրված է
YouTube-ում" and the link opens with a click."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reel import stages as st
from reel.db import DB
from reel.publish import slack
from reel.publish.errors import PublishError


def course(tmp_path, subjects=("Python", "React")):
    db = DB(tmp_path / "t.db")
    group = {"slack_channel": "C9", "subjects": [{"name": s, "playlist": ""} for s in subjects]}

    def job(name, subject, slack_id=None):
        jid = db.create_job(str(tmp_path / "lessons" / name), f"sha-{name}", "G", subject)
        if slack_id:
            db.add_publication(jid, "slack", slack_id, None)
        return jid

    return db, group, job


def ctx(db, group, jid):
    return SimpleNamespace(
        db=db,
        job_id=jid,
        group="G",
        config={"groups": {"G": group}},
        out=lambda n: {"youtube_upload": {"external_id": "vid", "url": "https://youtu.be/vid"}}[n],
        publish_once=lambda target, do, find=None: do() and {"external_id": "ts"},
    )


def test_number_counts_only_this_course_real_announcements(tmp_path):
    db, group, job = course(tmp_path)
    job("a", "Python", "ts1"), job("b", "python", "ts2"), job("c", "React", "ts3")
    job("d", "Python", "dry-ts"), job("e", "Python")  # dry run / not announced
    assert st._lesson_number(ctx(db, group, job("f", "Python")), "Python") == 3


def test_single_subject_group_counts_every_lesson(tmp_path):
    db, group, job = course(tmp_path, subjects=("Python",))
    job("a", None, "ts1")
    assert st._lesson_number(ctx(db, group, job("b", None)), "Python") == 2


@pytest.mark.parametrize(
    "name, n", [("5", 5), ("Դաս 5", 5), ("դաս-12", 12), ("das_03", 3), ("lesson 7", 7)]
)
def test_number_from_the_lesson_name(tmp_path, name, n):
    db, group, job = course(tmp_path)
    job("a", "Python", "ts1")
    assert st._lesson_number(ctx(db, group, job(name, "Python")), "Python") == n


@pytest.mark.parametrize("name", ["das-2026-11-19-1430", "2026", "0", "Group-5", "IMG_0360.MOV"])
def test_other_names_fall_back_to_counting(tmp_path, name):
    db, group, job = course(tmp_path)
    assert st._lesson_number(ctx(db, group, job(name, "Python")), "Python") == 1


@pytest.mark.parametrize(
    "name, ordinal", [("lesson-a", "1-ին"), ("Դաս 2", "2-րդ"), ("11", "11-րդ")]
)
def test_slack_line(tmp_path, name, ordinal):
    db, group, job = course(tmp_path)
    poster = MagicMock(return_value="ts")
    with (
        patch.object(st, "_pub", return_value=SimpleNamespace(post_link=poster)),
        patch.object(st, "_wait_youtube_ready"),
    ):
        st.slack_post(ctx(db, group, job(name, "Python")))
    assert poster.call_args.args == (
        "C9",
        f"Python դասընթացի {ordinal} դասի տեսանյութը տեղադրված է YouTube-ում",
        "https://youtu.be/vid",
    )


def test_link_is_clickable_and_title_cannot_add_markup():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.2"}
    with patch.object(slack, "_client", return_value=client):
        assert slack.post_link("C9", "A <!channel> & *b*", "https://youtu.be/vid") == "1.2"
    kwargs = client.chat_postMessage.call_args.kwargs
    assert (
        kwargs["text"] == "A &lt;!channel&gt; &amp; *b*\n<https://youtu.be/vid>"
        and kwargs["mrkdwn"] is True
    )


@pytest.mark.parametrize(
    "url", ["https://youtu.be/a|b", "https://youtu.be/a>b", "https://youtu.be/a b"]
)
def test_link_with_markup_characters_is_rejected(url):
    with patch.object(slack, "_client") as client, pytest.raises(PublishError):
        slack.post_link("C9", "t", url)
    client.assert_not_called()
