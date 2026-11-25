"""Owner, 2026-11-19: the students' Slack link goes out only after YouTube has finished processing
the video, with the first check immediate and later checks every 15 seconds; a rejected or stuck video stops the job
and nothing is sent to Slack."""

import time as real_time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from reel import stages as st
from reel.db import DB
from reel.hints import hint
from reel.pipeline import StopJob

GROUP = {"slack_channel": "C9", "subjects": [{"name": "Python", "playlist": "PL1"}]}


def ctx(tmp_path, uploaded_ago=0):
    db = DB(tmp_path / "t.db")
    jid = db.create_job(str(tmp_path / "x"), "sha", "G", "Python")
    db.add_publication(jid, "youtube", "vid", "https://youtu.be/vid")
    if uploaded_ago:
        when = (datetime.now(UTC) - timedelta(seconds=uploaded_ago)).isoformat(timespec="seconds")
        db.conn.execute("UPDATE publications SET created_at=? WHERE job_id=?", (when, jid))
    posted = []
    return SimpleNamespace(
        db=db,
        job_id=jid,
        group="G",
        config={"groups": {"G": GROUP}},
        workdir=tmp_path,
        out=lambda n: {
            "youtube_upload": {"external_id": "vid", "url": "https://youtu.be/vid"},
            "metadata": {"title": "Դաս՝ Closures"},
        }[n],
        publish_once=lambda target, do, find=None: posted.append((target, do()))
        or {"external_id": "ts"},
        posted=posted,
    )


def run(c, statuses):
    clock, sleeps = [real_time.time()], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    yt, slack = MagicMock(), MagicMock()
    yt.processing_status.side_effect = statuses
    slack.post_link.return_value = "ts"
    with (
        patch.object(st, "DRY_RUN", False),
        patch.object(st, "_pub", side_effect=lambda n: {"youtube": yt, "slack": slack}[n]),
        patch.object(st.time, "sleep", side_effect=sleep),
        patch.object(st.time, "time", side_effect=lambda: clock[0]),
        patch.object(st.time, "monotonic", side_effect=lambda: clock[0]),
    ):
        try:
            result = st.slack_post(c)
        except StopJob as exc:
            result = exc
    return result, sleeps, yt, slack


def test_slack_waits_until_youtube_has_processed_the_video(tmp_path):
    c = ctx(tmp_path)
    result, sleeps, yt, slack = run(c, ["uploaded", "uploaded", "processed"])
    assert st.YT_MIN_WAIT == 0 and st.YT_POLL == 15 and sleeps == [15, 15]
    assert yt.processing_status.call_count == 3 and slack.post_link.call_count == 1
    assert [t for t, _ in c.posted] == ["slack"]


def test_no_extra_wait_when_the_upload_is_old_and_processed(tmp_path):
    result, sleeps, _, slack = run(ctx(tmp_path, uploaded_ago=3600), ["processed"])
    assert sleeps == [] and slack.post_link.call_count == 1


@pytest.mark.parametrize("status", ["failed", "rejected", "deleted", "missing"])
def test_rejected_video_stops_without_slack(tmp_path, status):
    result, _, _, slack = run(ctx(tmp_path, uploaded_ago=3600), [status])
    assert isinstance(result, StopJob) and status in str(result)
    slack.post_link.assert_not_called()


def test_stuck_processing_stops_after_max_wait_without_slack(tmp_path):
    result, sleeps, _, slack = run(ctx(tmp_path, uploaded_ago=3600), lambda video_id: "uploaded")
    assert isinstance(result, StopJob) and "still processing" in str(result)
    assert st.YT_MAX_WAIT - st.YT_POLL <= sum(sleeps) <= st.YT_MAX_WAIT
    slack.post_link.assert_not_called()


def test_already_posted_slack_does_not_wait(tmp_path):
    c = ctx(tmp_path)
    c.db.add_publication(c.job_id, "slack", "ts", None)
    _, sleeps, yt, _ = run(c, ["uploaded"])
    assert sleeps == [] and yt.processing_status.call_count == 0


def test_stop_reasons_have_armenian_hints():
    assert "YouTube Studio" in hint(
        "[slack_post] YouTube is still processing the video after 60 min — Slack not sent"
    )
    assert "YouTube Studio" in hint(
        "[slack_post] YouTube did not accept the video (status 'failed') — Slack not sent"
    )
