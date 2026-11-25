"""Chapterless metadata must still reach every publication consumer safely."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel import stages as st
from reel import thumbnail as thumbnail_module
from reel.db import DB
from reel.pipeline import Ctx
from reel.stages import metadata as md


def test_default_metadata_reaches_all_consumers_and_publication_is_idempotent(
    tmp_path, monkeypatch
):
    """A failed chapter plan is an annotation, not a publication failure."""
    monkeypatch.setattr(st, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("chapterless metadata must not notify, run media tools, or call live adapters")

    monkeypatch.setattr(st, "_notify", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    source = tmp_path / "uploads" / "Python_lesson_26.mp4"
    config = {
        "version": 2,
        "groups": {
            "fixture-group": {
                "subjects": [{"name": "Python", "playlist": "PL-fixture"}],
                "slack_channel": "C-FIXTURE",
                "lecturers": [],
            }
        },
    }
    db = DB(tmp_path / "reel.db")
    try:
        job_id = db.create_job(str(source), "metadata-consumers-fixture", "fixture-group")
        assert job_id is not None
        db.set_job(job_id, status="running", created_at="2026-11-19T10:20:30+00:00")
        workdir = tmp_path / "work" / str(job_id)
        transcript = workdir / "transcript" / "transcript.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "segments": [
                        {"start": 0.0, "end": 3000.0, "text": "Python-ի generator-ների աշխատանքը։"},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        final = str(workdir / "final_blurred.mp4")
        events = []
        maps = []
        plans = []

        def fake_chat(system, user, **kwargs):
            assert system == md.MAP_SYS, "planning failure must not invoke REDUCE"
            maps.append((user, kwargs))
            return {
                "topic": "Python Generators",
                "summary": "Generator execution and iterators.",
                "topic_changes": [{"t": 0.0, "topic": "Python Generators"}],
            }

        def failed_plan(summaries, keep):
            plans.append((copy.deepcopy(summaries), copy.deepcopy(keep)))
            raise ValueError("fixture: insufficient grounded chapter topics")

        monkeypatch.setattr(md.llm, "chat_json", fake_chat)
        monkeypatch.setattr(md, "_chapter_plan", failed_plan)

        def make_thumbnail(video, title, out_path, group=None):
            events.append(("thumbnail", video, title, out_path))
            return out_path

        def find_by_tag(tag):
            events.append(("youtube.find", tag))
            return None

        def upload(video, title, description, tags, *, privacy):
            events.append(("youtube.upload", video, title, description, tags, privacy))
            return {
                "video_id": "fixture-video",
                "url": "https://www.youtube.com/watch?v=fixture-video",
            }

        def post_link(channel, line, url):
            events.append(("slack.post_link", channel, line, url))
            return "12345.6789"

        def library_add(entry):
            events.append(("library.add", copy.deepcopy(entry)))

        publishers = {
            "youtube": SimpleNamespace(
                find_by_tag=find_by_tag,
                upload=upload,
                processing_status=lambda video_id: "processed",
            ),
            "slack": SimpleNamespace(post_link=post_link),
            "library": SimpleNamespace(add=library_add),
        }
        monkeypatch.setattr(thumbnail_module, "make_thumbnail", make_thumbnail)
        monkeypatch.setattr(st, "_pub", lambda name: publishers[name])
        monkeypatch.setattr(st, "YT_MIN_WAIT", 0)  # no real 5-minute wait in tests

        def context():
            # Fresh caches exercise the persisted outputs used on resume.
            return Ctx(db, job_id, source, workdir, "fixture-group", config)

        def save_output(name, output):
            db.stage_start(job_id, name)
            db.stage_done(job_id, name, output)

        def run_stage(name, function):
            db.stage_start(job_id, name)
            output = function(context())
            db.stage_done(job_id, name, output)
            assert db.stage(job_id, name)["status"] == "done"
            return output

        save_output("segments", {"keep": [[0.0, 3000.0]], "cuts": []})
        save_output("blur", {"final": final})
        metadata = run_stage("metadata", st.metadata_stage)
        artifact = json.loads((workdir / "metadata.json").read_text(encoding="utf-8"))
        assert len(maps) == len(plans) == 1
        assert plans[0][1] == [(0.0, 3000.0)]
        assert plans[0][0][0]["topic"] == "Python Generators"
        assert artifact["chapters"] == metadata["chapters"] == []
        assert isinstance(artifact.get("auto_note"), str) and artifact["auto_note"].strip()
        assert artifact["generation_mode"] == "default"
        title, description = metadata["title"], metadata["description"]
        assert title == artifact["title"]
        assert description == artifact["youtube_description"]
        assert source.stem in title and "2026-11-19" in title
        assert 0 < len(title) <= 100 and 0 < len(description) <= 5000
        assert re.search(r"[Ա-Ֆա-և]", title) and re.search(r"[Ա-Ֆա-և]", description)
        assert artifact["description"].strip() in description
        assert not re.search(r"(?m)^\s*\d+:\d{2}(?::\d{2})?\s", description)
        assert not any(marker in title + description for marker in ("`", "**", "<", ">"))

        thumbnail = run_stage("thumbnail", st.thumbnail)
        youtube = run_stage("youtube_upload", st.youtube_upload)
        slack = run_stage("slack_post", st.slack_post)
        library = run_stage("library_add", st.library_add)
        url = "https://www.youtube.com/watch?v=fixture-video"
        assert thumbnail == {"path": str(workdir / "thumbnail.jpg")}
        assert youtube == {
            "external_id": "fixture-video",
            "url": url,
            "reused": False,
            "lesson_no": 1,
        }
        assert slack == {"external_id": "12345.6789", "url": None, "reused": False}
        assert library == {"external_id": None, "url": None, "reused": False}
        assert [event[0] for event in events] == [
            "thumbnail",
            "youtube.find",
            "youtube.upload",
            "slack.post_link",
            "library.add",
        ]
        assert events[0] == ("thumbnail", final, title, str(workdir / "thumbnail.jpg"))
        assert events[1] == ("youtube.find", f"reel-{job_id}")
        assert events[2] == (
            "youtube.upload",
            final,
            st._youtube_title("Python", 1, title),
            description,
            [f"reel-{job_id}", "fixture-group"],
            "unlisted",
        )
        assert events[3] == (
            "slack.post_link",
            "C-FIXTURE",
            "Python դասընթացի 1-ին դասի տեսանյութը տեղադրված է YouTube-ում",
            url,
        )
        entry = events[4][1]
        assert {key: entry[key] for key in ("job_id", "group", "subject", "title", "url")} == {
            "job_id": str(job_id),
            "group": "fixture-group",
            "subject": "Python",
            "title": title,
            "url": url,
        }
        assert datetime.fromisoformat(entry["published_at"]).utcoffset() is not None
        assert not source.exists() and not Path(final).exists()
        assert not (workdir / "thumbnail.jpg").exists()

        # Real publish_once records make resumed consumers skip all external effects.
        published_events = copy.deepcopy(events)
        for target, function in (
            ("youtube", st.youtube_upload),
            ("slack", st.slack_post),
            ("library", st.library_add),
        ):
            assert db.publication(job_id, target) is not None
            assert function(context())["reused"] is True
        assert events == published_events
        assert db.conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 3
        assert not list(workdir.glob("pub_*.pending"))
    finally:
        db.conn.close()
