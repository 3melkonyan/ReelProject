"""Armenian operator hints for stopped jobs (notifications + dashboard)."""
import tempfile
from pathlib import Path

from reel import pipeline
from reel.db import DB
from reel.hints import AMBIGUOUS, hint


def test_hint_by_stage_and_ambiguous_publish():
    assert "դեմք" in hint("[privacy] 3 non-lecturer face event(s) visible after blur")
    assert hint("[slack_post] 'slack' may already be posted (crash during publish)") == AMBIGUOUS
    assert hint(None) is None and hint("no stage prefix") is None and hint("[unknown] x") is None


def test_stop_notification_carries_the_hint():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        db = DB(root / "reel.db")
        src = root / "lesson.mp4"; src.write_bytes(b"v")
        jid = db.create_job(str(src), "sha-h", "G")
        msgs = []

        def privacy(ctx):
            raise pipeline.StopJob("2 non-lecturer face event(s) visible after blur")
        assert pipeline.run_job(db, jid, [("privacy", privacy)], {"groups": {"G": {}}}, root,
                                notify=msgs.append) == "stopped"
        assert "💡" in msgs[-1] and "Գաղտնիություն" not in msgs[-1] or "դեմք" in msgs[-1]
