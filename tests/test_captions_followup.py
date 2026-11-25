"""Optional captions stay optional, and compressed timelines never use a stale mapping."""

import json
from contextlib import closing
from pathlib import Path

import pytest

from reel import learning
from reel.db import DB
from reel.pipeline import Ctx
from reel.stages import learning_stages


def seed(tmp_path):
    db = DB(tmp_path / "test.db")
    job = db.create_job("fixture.mp4", "fixture", "Group-A")
    db.stage_start(job, "segments")
    db.stage_done(job, "segments", {"keep": [[0, 100]], "time_map": [[0, 100, 0, 80]]})
    folder = tmp_path / "work" / str(job)
    (folder / "transcript").mkdir(parents=True)
    (folder / "transcript/transcript.json").write_text(
        json.dumps({"segments": [{"start": 60, "end": 70, "text": "Անվստահ ժամանակ"}]})
    )
    return db, job, folder


def test_compressed_timeline_requires_final_transcript_and_recovers(tmp_path):
    db, job, folder = seed(tmp_path)
    with closing(db.conn):
        assert learning.transcript_cues(db, job, tmp_path / "work") == (
            [],
            "missing_final_timeline",
        )
        learning.ensure_cues(db, [job], tmp_path / "work")
        assert learning.lesson_cues(db, job) == []
        (folder / "transcript_final.json").write_text(
            json.dumps({"segments": [{"start": 40, "end": 45, "text": "Ճիշտ ժամանակ"}]})
        )
        learning.ensure_cues(db, [job], tmp_path / "work")
        assert learning.lesson_cues(db, job) == [
            {"start": 40.0, "end": 45.0, "text": "Ճիշտ ժամանակ"}
        ]


@pytest.mark.parametrize("failure", ["storage", "adapter", "lookup"])
def test_every_caption_side_effect_is_optional(tmp_path, monkeypatch, failure):
    from reel import stages

    db, job, folder = seed(tmp_path)
    with closing(db.conn):
        (folder / "transcript_final.json").write_text(
            json.dumps({"segments": [{"start": 0, "end": 1, "text": "Թեստային նյութ"}]})
        )
        db.stage_start(job, "youtube_upload")
        db.stage_done(job, "youtube_upload", {"external_id": "abcdefghijk"})

        def fail(*args, **kwargs):
            raise OSError("must-not-leak-private-detail")

        if failure == "storage":
            monkeypatch.setattr(Path, "write_text", fail)
        elif failure == "adapter":
            monkeypatch.setattr(stages, "_pub", fail)
        else:
            monkeypatch.setattr(learning, "save_cues", fail)
        result = learning_stages.captions_stage(
            Ctx(db, job, tmp_path / "in.mp4", folder, "Group-A", {})
        )
        assert result["skipped"] == "OSError" and result.get("auto_note")
        assert "must-not-leak-private-detail" not in str(result)


@pytest.mark.parametrize("final", [False, True])
def test_array_transcripts_support_processing_time_map(tmp_path, final):
    db, job, folder = seed(tmp_path)
    with closing(db.conn):
        segments = [{"start": 10, "end": 20, "text": "Համատեղելի տեքստ"}]
        if final:
            (folder / "transcript_final.json").write_text(json.dumps(segments))
        else:
            db.stage_done(job, "segments", {"keep": [[0, 100]]})
            (folder / "transcript/transcript.json").write_text(json.dumps(segments))
        cues, source = learning.transcript_cues(db, job, tmp_path / "work")
        assert cues == segments and source == ("final" if final else "transcript")
