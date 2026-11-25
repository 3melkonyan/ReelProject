"""Owner, 2026-11-19: the YouTube title is «Python | Դաս 5 | <topic>», with the same lesson
number as the Slack line."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reel import stages as st
from reel.db import DB


@pytest.mark.parametrize(
    "topic, expected",
    [
        ("Closures և Context", "Python | Դաս 5 | Closures և Context"),
        ("Դաս՝ Hash Table և Collision", "Python | Դաս 5 | Hash Table և Collision"),
        ("  a <b>  c ", "Python | Դաս 5 | a b c"),
        ("", "Python | Դաս 5"),
    ],
)
def test_title_format(topic, expected):
    assert st._youtube_title("Python", 5, topic) == expected


def test_long_topic_uses_the_first_thought_within_70_chars_without_ellipsis():
    title = st._youtube_title("JavaScript", 12, "Hash Table Implementation և Collision, " * 5)
    assert title == "JavaScript | Դաս 12 | Hash Table Implementation և Collision"
    assert len(title) <= 70 and not title.endswith("…")


def test_upload_uses_the_numbered_title_and_slack_reuses_its_number(tmp_path):
    db = DB(tmp_path / "t.db")
    group = {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": ""}]}
    db.add_publication(db.create_job(str(tmp_path / "a"), "sha-a", "G", None), "slack", "ts1", None)
    jid = db.create_job(str(tmp_path / "b"), "sha-b", "G", None)
    outputs = {
        "metadata": {"title": "Դաս՝ Closures", "description": "d"},
        "blur": {"final": "f.mp4"},
    }
    c = SimpleNamespace(
        db=db,
        job_id=jid,
        group="G",
        config={"groups": {"G": group}},
        workdir=tmp_path,
        out=lambda n: outputs[n],
        publish_once=lambda target, do, find=None: dict(
            zip(("external_id", "url"), do(), strict=False)
        ),
    )
    yt = MagicMock()
    yt.upload.return_value = {"video_id": "vid", "url": "https://youtu.be/vid"}
    with patch.object(st, "_pub", return_value=yt):
        outputs["youtube_upload"] = st.youtube_upload(c)
    assert yt.upload.call_args.args[1] == "Python | Դաս 2 | Closures"
    assert outputs["youtube_upload"]["lesson_no"] == 2
    other = db.create_job(str(tmp_path / "c"), "sha-c", "G", None)
    db.add_publication(other, "slack", "ts2", None)  # counting again would now say 3
    poster = MagicMock(return_value="ts")
    with (
        patch.object(st, "_pub", return_value=SimpleNamespace(post_link=poster)),
        patch.object(st, "_wait_youtube_ready"),
    ):
        st.slack_post(c)
    assert (
        poster.call_args.args[1] == "Python դասընթացի 2-րդ դասի տեսանյութը տեղադրված է YouTube-ում"
    )
