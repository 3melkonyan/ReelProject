"""Thumbnail_set: a fresh upload may refuse the thumbnail for a minute; retry, never block publishing."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from reel import stages as st

OUTS = {"thumbnail": {"path": "thumb.jpg"}, "youtube_upload": {"external_id": "vid1"}}


def run(side_effect):
    yt = MagicMock()
    yt.set_thumbnail.side_effect = side_effect
    ctx = SimpleNamespace(job_id=7, out=lambda name: OUTS[name])
    with patch.object(st, "_pub", return_value=yt), patch.object(st.time, "sleep") as sleep, \
            patch.object(st, "_notify") as notify:
        result = st.thumbnail_set(ctx)
    return result, [c.args[0] for c in sleep.call_args_list], notify


def test_retry_after_early_403_succeeds_quietly():
    result, waits, notify = run([Exception("set_thumbnail: Google API HTTP 403"), None])
    assert result == {"set": True, "attempts": 2} and waits == [0, 60] and not notify.called


def test_gives_up_after_three_tries_with_a_note_and_no_slack_message():
    result, waits, notify = run(Exception("HTTP 403"))
    assert result["set"] is False and result["attempts"] == 3 and waits == [0, 60, 120]
    assert notify.call_count == 0 and "published without it" in result["auto_note"]


def test_no_thumbnail_file_skips():
    ctx = SimpleNamespace(job_id=7, out=lambda name: {"thumbnail": {"skipped": "x"}}[name])
    assert st.thumbnail_set(ctx) == {"skipped": True}
