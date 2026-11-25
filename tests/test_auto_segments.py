"""Cut-planning uncertainty keeps the complete lesson, using mocked signals only."""
from __future__ import annotations

import json

import pytest

from reel.pipeline import StopJob
from reel.stages import segments


@pytest.fixture
def planner(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("auto-segments tests must not run ffmpeg or an LLM")

    monkeypatch.setattr(segments.subprocess, "run", forbidden)
    monkeypatch.setattr(segments.llm, "chat_json", forbidden)
    calls = []

    def run(*, total=3600.125, config=None, unclear=0, off_topic=(), silences=()):
        transcript = tmp_path / "transcript.json"
        transcript.write_text(json.dumps({"segments": [{"start": 0, "end": total,
                                                          "text": "Lesson transcript fixture"}]}))

        def quiet(*args):
            calls.append("quiet")
            return []

        def labels(windows):
            calls.append("labels")
            count = len(windows) if unclear == "all" else unclear
            return ["unclear"] * count + ["lesson"] * (len(windows) - count)

        monkeypatch.setattr(segments, "detect_silences", quiet)
        monkeypatch.setattr(segments, "label_windows", labels)
        monkeypatch.setattr(segments, "offtopic_cuts", lambda *args: list(off_topic))
        monkeypatch.setattr(segments, "silence_cuts", lambda *args: list(silences))
        # These tests isolate the final guards from sentence evidence.
        monkeypatch.setattr(segments, "_edges", lambda rows, duration, cfg: (0., duration, {}))
        monkeypatch.setattr(segments, "_middle_cuts", lambda *args: (list(off_topic) + list(silences), []))
        return segments.plan("unused-video.mp4", "unused-audio.wav", transcript, total,
                             {"pad_sec": 0, **(config or {})}, tmp_path / "plan")

    run.path = tmp_path / "plan" / "segments.json"
    run.calls = calls
    return run


def assert_full_lesson(planner, result, total, prefix):
    assert result["keep"] == [[0.0, total]]
    assert result["cuts"] == []
    assert result["original_sec"] == result["final_sec"] == total
    assert result["cut_ratio"] == 0.0
    assert result["auto_note"].startswith(prefix)
    assert result["auto_note"].endswith(" — nothing cut")
    assert json.loads(planner.path.read_text()) == result
    assert sum(end - start for start, end in result["keep"]) == total


def test_unclear_windows_discard_every_proposed_cut_and_keep_precise_total(planner):
    total = 3600.125
    result = planner(total=total, unclear="all", off_topic=[(0, 600, "before_class")],
                     silences=[(1200, 1500, "silence")])
    assert_full_lesson(planner, result, total, "unclear windows ")
    assert result["unclear_ratio"] == 1.0 and result["labels"]


def test_excessive_cut_ratio_returns_full_lesson_instead_of_stopping(planner):
    total = 3600.125
    result = planner(total=total, off_topic=[(600, 2400, "break")])
    assert_full_lesson(planner, result, total, "plan cut ")


def test_result_below_old_minimum_keeps_safe_cuts(planner):
    total = 2400.125
    result = planner(total=total, silences=[(500, 1200, "silence")])
    assert result["keep"] == [[0.0, 500], [1200, total]]
    assert result["cuts"] == [{"start": 500, "end": 1200, "why": "silence"}]
    assert result["final_sec"] == round(total - 700, 1)
    assert result["no_cut_code"] is None and "auto_note" not in result
    assert json.loads(planner.path.read_text()) == result


def test_cut_ratio_guard_uses_unrounded_value_before_resetting_report(planner):
    result = planner(total=10000, off_topic=[(1000, 5004, "break")])
    assert round(4004 / 10000, 3) == 0.4
    assert_full_lesson(planner, result, 10000, "plan cut ")
    assert "40.04%" in result["auto_note"]


def test_first_safety_reason_wins_when_multiple_checks_fail(planner):
    result = planner(total=3600, unclear="all", off_topic=[(0, 2400, "before_class")])
    assert_full_lesson(planner, result, 3600, "unclear windows ")


def test_valid_plan_still_applies_real_cuts_without_an_auto_note(planner):
    result = planner(total=3600, off_topic=[(0, 300, "before_class")],
                     silences=[(1200, 1500, "silence")])
    assert result["keep"] == [[300, 1200], [1500, 3600]]
    assert result["cuts"] == [{"start": 0, "end": 300, "why": "before_class"},
                               {"start": 1200, "end": 1500, "why": "silence"}]
    assert result["final_sec"] == 3000 and result["cut_ratio"] == 0.167
    assert "auto_note" not in result
    assert json.loads(planner.path.read_text()) == result


def test_normal_full_lesson_has_no_fallback_note(planner):
    result = planner(total=3600)
    assert result["keep"] == [[0.0, 3600]] and result["cuts"] == []
    assert "auto_note" not in result
    assert json.loads(planner.path.read_text()) == result


def test_safety_thresholds_are_strict_and_allow_equal_values(planner):
    result = planner(total=3000, config={"window_sec": 30}, unclear=30,
                     off_topic=[(0, 1200, "before_class")])
    assert result["unclear_ratio"] == 0.3 and result["cut_ratio"] == 0.4
    assert result["final_sec"] == 1800 and result["keep"] == [[1200, 3000]]
    assert "auto_note" not in result
    assert json.loads(planner.path.read_text()) == result


@pytest.mark.parametrize("size", [0, -60, float("nan"), float("inf"), -float("inf"), None, "60", True, False])
def test_invalid_window_configuration_still_stops_before_any_signals_or_artifact(planner, size):
    with pytest.raises(StopJob, match="cut.window_sec"):
        planner(config={"window_sec": size})
    assert planner.calls == [] and not planner.path.exists()
