"""Job 4 (first live run): library_add passed the int job id and the real library refused it."""

import json

import reel.publish.library as lib
import reel.stages as st
from reel.pipeline import Ctx


def test_library_add_writes_a_record_with_a_string_job_id(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "DRY_RUN", False)  # the dry-run fake never validated
    monkeypatch.setattr(lib, "_path", lambda: tmp_path / "library.jsonl")
    monkeypatch.setattr(st, "_subject", lambda ctx: {"name": "Math"})
    c = Ctx(None, 4, tmp_path / "src.mp4", tmp_path / "work", "G", {"groups": {"G": {}}})
    c._outputs.update(
        youtube_upload={"url": "https://www.youtube.com/watch?v=x"}, metadata={"title": "T"}
    )
    c.publish_once = lambda kind, fn: fn()
    st.library_add(c)
    rec = json.loads((tmp_path / "library.jsonl").read_text(encoding="utf-8"))
    assert rec["job_id"] == "4" and rec["subject"] == "Math" and rec["url"].endswith("v=x")
