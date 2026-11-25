"""Owner, 2026-11-19: playlists are not created yet. The admin adds groups and subjects in
Settings without a playlist, and such a lesson is published without one (never stopped)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reel import stages as st
from reel.config import validate_config
from reel.db import DB
from reel.web import admin


def config(subjects):
    return {"operator_slack_channel": "C0", "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
            "groups": {"G": {"slack_channel": "C9", "subjects": subjects}}}


def test_subjects_may_have_no_playlist():
    cfg = validate_config(config([{"name": "Python"}, {"name": "React", "playlist": ""},
                                  {"name": "JS", "playlist": " PL1 "}, {"name": "Go", "playlist": None}]))
    assert [s["playlist"] for s in cfg["groups"]["G"]["subjects"]] == ["", "", "PL1", ""]


def test_real_playlists_are_still_unique_within_a_group():
    with pytest.raises(ValueError, match="unique"):
        validate_config(config([{"name": "A", "playlist": "PL1"}, {"name": "B", "playlist": "PL1"}]))
    with pytest.raises(ValueError):
        validate_config(config([{"playlist": "PL1"}]))               # a name is still required


def test_settings_accept_an_empty_playlist():
    assert admin._playlist("") == "" and admin._playlist("   ") == ""
    assert admin._playlist("https://www.youtube.com/playlist?list=PL_x-1") == "PL_x-1"


def test_lesson_without_playlist_is_published_without_one(tmp_path):
    group = {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": ""}]}
    db = DB(tmp_path / "t.db")
    calls = []
    c = SimpleNamespace(db=db, job_id=db.create_job(str(tmp_path / "x"), "sha", "G", "Python"), group="G",
                        config={"groups": {"G": group}}, workdir=tmp_path,
                        out=lambda n: {"youtube_upload": {"external_id": "vid", "url": "https://youtu.be/vid"}}[n],
                        publish_once=lambda target, do, find=None: calls.append(target) or {"external_id": "x"})
    yt = MagicMock()
    with patch.object(st, "_pub", return_value=yt):
        result = st.playlist(c)
    assert result["skipped"] and result["subject"] == "Python"
    yt.add_to_playlist.assert_not_called()
    assert calls == []
