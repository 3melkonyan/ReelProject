"""A lesson belongs to ONE subject of its group: that subject's playlist, a Slack line naming it,
and nothing is published while the subject is unknown."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reel import stages as st
from reel.db import DB
from reel.hints import hint
from reel.pipeline import StopJob

GROUP = {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": "PL1"},
                                             {"name": "JavaScript", "playlist": "PL2"}]}


def ctx(tmp_path, subject=None, group=GROUP, published=None):
    db = DB(tmp_path / "t.db")
    jid = db.create_job(str(tmp_path / "x"), f"sha-{subject}-{len(group['subjects'])}", "G", subject)
    calls = published if published is not None else []
    return SimpleNamespace(db=db, job_id=jid, group="G", config={"groups": {"G": group}},
                           workdir=tmp_path,
                           out=lambda n: {"youtube_upload": {"external_id": "vid", "url": "https://youtu.be/vid"},
                                          "metadata": {"title": "Դաս՝ Closures", "description": "d"}}[n],
                           publish_once=lambda target, do, find=None: calls.append((target, do())) or {"external_id": "x"})


def test_old_database_gets_the_subject_column(tmp_path):
    con = sqlite3.connect(tmp_path / "old.db")
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,"
                " grp TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', current_stage TEXT, error TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.close()
    d = DB(tmp_path / "old.db")
    assert d.job(d.create_job("/x", "s", "G", "Python"))["subject"] == "Python"


def test_chosen_subject_decides_playlist_and_slack_line(tmp_path):
    calls = []
    c = ctx(tmp_path, subject="javascript", published=calls)          # case-insensitive match
    yt, slack = MagicMock(), MagicMock()
    yt.add_to_playlist.return_value = "item"
    slack.post_link.return_value = "ts"
    yt.processing_status.return_value = "processed"
    with patch.object(st, "_pub", side_effect=lambda name: {"youtube": yt, "slack": slack}[name]), \
            patch.object(st, "DRY_RUN", False), patch.object(st.time, "sleep"):
        assert st.playlist(c)["playlist"] == "PL2"
        st.slack_post(c)
    yt.add_to_playlist.assert_called_once_with("vid", "PL2")
    channel, line, url = slack.post_link.call_args.args
    assert channel == "C9" and line == "JavaScript դասընթացի 1-ին դասի տեսանյութը տեղադրված է YouTube-ում" and url.endswith("vid")
    assert [t for t, _ in calls] == ["playlist", "slack"]


def test_single_subject_group_needs_no_choice(tmp_path):
    one = {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": "PL1"}]}
    assert st._subject(ctx(tmp_path, group=one))["playlist"] == "PL1"


def test_unknown_subject_stops_before_anything_is_published(tmp_path):
    for c in (ctx(tmp_path, subject=None), ctx(tmp_path, subject="Go")):
        with patch.object(st, "_pub") as pub, pytest.raises(StopJob, match="choose the lesson's subject"):
            st.youtube_upload(c)
        pub.assert_not_called()
    assert "առարկ" in hint("[youtube_upload] group 'G' has 2 subjects — choose the lesson's subject and retry")
