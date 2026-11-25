"""Classroom is optional per group: no course id -> the stage is skipped, nothing is posted."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from reel import stages as st
from reel.config import load_config

BASE = {
    "operator_slack_channel": "C1",
    "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
    "groups": {"G": {"playlist_id": "PL1", "slack_channel": "C1", "lecturers": ["l1"]}},
}


def write(tmp_path, cfg):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(p)


def test_config_without_or_with_empty_classroom_loads(tmp_path):
    assert "classroom_course_id" not in load_config(write(tmp_path, BASE))["groups"]["G"]
    BASE["groups"]["G"]["classroom_course_id"] = ""
    try:
        assert (
            "classroom_course_id" not in load_config(write(tmp_path, BASE))["groups"]["G"]
        )  # empty = off
    finally:
        BASE["groups"]["G"].pop("classroom_course_id")


def test_unknown_or_missing_required_group_keys_still_rejected(tmp_path):
    bad = {**BASE, "groups": {"G": {**BASE["groups"]["G"], "clasroom": "typo"}}}
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))
    bad = {**BASE, "groups": {"G": {"slack_channel": "C1", "lecturers": ["l1"]}}}
    with pytest.raises(ValueError):
        load_config(write(tmp_path, bad))


def ctx(group):
    return SimpleNamespace(
        job_id=5,
        group="G",
        config={"groups": {"G": group}},
        out=lambda n: {"metadata": {"title": "T"}, "youtube_upload": {"url": "https://y"}}[n],
        publish_once=MagicMock(return_value={"external_id": "a1"}),
    )


def test_stage_skips_when_classroom_is_off():
    c = ctx({"playlist_id": "PL1"})
    with patch.object(st, "_pub") as pub:
        assert st.classroom_post(c) == {"skipped": "Classroom is off for this group"}
    pub.assert_not_called()
    c.publish_once.assert_not_called()


def test_stage_posts_when_course_is_set():
    c = ctx({"classroom_course_id": "123"})
    with patch.object(st, "_pub"):
        assert st.classroom_post(c) == {"external_id": "a1"}
    assert c.publish_once.call_args.args[0] == "classroom"
