"""Cutting rules: temporary artifacts and fake signals, never media or LLM calls."""

import copy
import json

import pytest

from reel.config import load_config
from reel.insights import cut_summary
from reel.stages import MIN_LESSON_SEC, processing, segments


@pytest.fixture
def planner(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No ffmpeg or LLM calls in cutting-rule tests")

    monkeypatch.setattr(segments.subprocess, "run", forbidden)
    monkeypatch.setattr(segments.llm, "chat_json", forbidden)

    def run(*, total=900.0, cuts=(), unclear=0, broken=0, config=None):
        raw = [{"start": 0, "end": total, "text": "Դասի խոսք"}]
        raw += [{"start": 5, "end": 1, "text": "Վնասված ժամանակ"} for _ in range(broken)]
        transcript = tmp_path / "transcript.json"
        transcript.write_text(json.dumps({"segments": raw}), encoding="utf-8")
        monkeypatch.setattr(segments, "detect_silences", lambda *args: [])
        monkeypatch.setattr(
            segments,
            "label_windows",
            lambda wins: ["unclear"] * unclear + ["lesson"] * (len(wins) - unclear),
        )
        monkeypatch.setattr(segments, "_edges", lambda rows, duration, cfg: (0.0, duration, {}))
        monkeypatch.setattr(segments, "offtopic_cuts", lambda *args: [])
        monkeypatch.setattr(segments, "silence_cuts", lambda *args: [])
        monkeypatch.setattr(segments, "_middle_cuts", lambda *args: (list(cuts), []))
        result = segments.plan(
            "unused-video", "unused-audio", transcript, total, config, tmp_path / "out"
        )
        assert json.loads((tmp_path / "out/segments.json").read_text()) == result
        return result

    return run


def test_fifteen_minute_lesson_applies_safe_cuts_without_a_minimum_final_length(planner):
    result = planner(cuts=[(300.0, 480.0, "break")])
    assert "min_final_sec" not in segments.DEFAULTS and MIN_LESSON_SEC == 60
    assert result["keep"] == [[0.0, 300.0], [480.0, 900.0]] and result["final_sec"] == 720.0
    assert (
        result["cut_ratio"] == 0.2 and result["no_cut_code"] is None and "auto_note" not in result
    )
    assert cut_summary(result)["no_cuts_reason"] is None


def test_legacy_config_min_final_sec_loads_and_is_ignored(planner, tmp_path):
    raw = {
        "groups": {"A": {"subjects": [{"name": "Python", "playlist": ""}], "slack_channel": "C"}},
        "operator_slack_channel": "C",
        "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4, "min_final_sec": 1800},
    }
    path = tmp_path / "legacy.yaml"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_config(str(path))
    result = planner(cuts=[(300.0, 480.0, "break")], config=config["cut"])
    assert config["cut"]["min_final_sec"] == 1800
    assert result["final_sec"] == 720 and result["no_cut_code"] is None
    assert (
        planner(cuts=[(300.0, 480.0, "break")], config={"min_final_sec": 10**9})["keep"]
        == result["keep"]
    )


@pytest.mark.parametrize(
    "amount,blocked,percent", [(360.0, False, None), (405.0, True, 45.0), (360.36, True, 40.04)]
)
def test_total_cut_budget_is_unrounded_inclusive_at_forty_percent(
    planner, amount, blocked, percent
):
    result = planner(cuts=[(100.0, 100.0 + amount, "break")])
    if blocked:
        assert (
            result["keep"] == [[0.0, 900.0]] and result["cuts"] == [] and result["cut_ratio"] == 0
        )
        assert result["no_cut_code"] == "over_max_ratio"
        assert result["no_cut_details"]["percent"] == pytest.approx(percent)
        assert result["no_cut_details"]["limit"] == 40
    else:
        assert result["final_sec"] == 540 and result["cut_ratio"] == 0.4
        assert result["no_cut_code"] is None and "auto_note" not in result


@pytest.mark.parametrize(
    "arguments,code,reason",
    [
        (
            {},
            "none_found",
            "Դասից դուրս հատվածներ և երկար դադարներ չեն գտնվել․ ամբողջ դասը պահպանվել է։",
        ),
        (
            {"broken": 3},
            "broken_timestamps",
            "Խոսքի ժամանակագրությունը վնասված էր․ անվտանգության համար ոչինչ չի կտրվել։",
        ),
        (
            {"unclear": 6},
            "unclear",
            "Դասի 40%-ը հնարավոր չեղավ հստակ դասակարգել (սահմանը՝ 30%)․ ոչինչ չի կտրվել։",
        ),
        (
            {"cuts": [(100.0, 505.0, "break")]},
            "over_max_ratio",
            "Ծրագրված կտրումը 45% էր, թույլատրելին՝ 40%․ ոչինչ չի կտրվել։",
        ),
    ],
)
def test_every_no_cut_code_has_the_exact_armenian_reason(planner, arguments, code, reason):
    result = planner(**arguments)
    summary = cut_summary(result)
    assert result["no_cut_code"] == summary["no_cut_code"] == code
    assert summary["no_cuts_reason"] == reason and "Անորոշ պլան" not in summary["no_cuts_reason"]


@pytest.mark.parametrize(
    "note,code,reason",
    [
        (
            "3 transcript segments have broken timestamps — nothing cut",
            "broken_timestamps",
            "Խոսքի ժամանակագրությունը վնասված էր․ անվտանգության համար ոչինչ չի կտրվել։",
        ),
        (
            "unclear windows 33.3% (> 30.0%) — nothing cut",
            "unclear",
            "Դասի 33.3%-ը հնարավոր չեղավ հստակ դասակարգել (սահմանը՝ 30%)․ ոչինչ չի կտրվել։",
        ),
        (
            "plan cut 40.04% (> 40.00%) — nothing cut",
            "over_max_ratio",
            "Ծրագրված կտրումը 40.04% էր, թույլատրելին՝ 40%․ ոչինչ չի կտրվել։",
        ),
        (
            "planned result too short 900.1 sec (< 1800 sec) — nothing cut",
            None,
            "Կտրում չի կատարվել։",
        ),
        ("plan failed", None, "Կտրում չի կատարվել։"),
        ("", None, "Կտրում չի կատարվել։"),
    ],
)
def test_legacy_auto_notes_are_inferred_without_calling_unknown_notes_unclear(note, code, reason):
    result = cut_summary(
        {"keep": [[0, 900]], "cuts": [], "original_sec": 900, "final_sec": 900, "auto_note": note}
    )
    assert result["no_cut_code"] == code and result["no_cuts_reason"] == reason


def test_structured_code_and_percentages_override_stale_human_note():
    result = cut_summary(
        {
            "cuts": [],
            "no_cut_code": "over_max_ratio",
            "no_cut_details": {"percent": 45, "limit": 40},
            "auto_note": "unclear windows 90.0% (> 30.0%) — nothing cut",
        }
    )
    assert (
        result["no_cuts_reason"] == "Ծրագրված կտրումը 45% էր, թույլատրելին՝ 40%․ ոչինչ չի կտրվել։"
    )
    assert (
        cut_summary({"cuts": [], "no_cut_code": "future_policy"})["no_cuts_reason"]
        == "Կտրում չի կատարվել։"
    )


def test_pauses_and_speedups_share_the_total_forty_percent_budget(planner, monkeypatch):
    base = planner(total=100.0, cuts=[(65.0, 100.0, "after_class")])
    monkeypatch.setattr(
        segments, "detect_silences", lambda *args: [(3.0, 7.0), (15.0, 19.0), (30.0, 34.0)]
    )
    monkeypatch.setattr(
        processing, "board_motion", lambda video, start, *args: 5 if 15 < start < 20 else 0
    )
    options = copy.deepcopy(processing.DEFAULTS["smart_pauses"])
    result = processing.pauses(
        base, "unused-video", "unused-audio", {"segments": []}, {}, options, 0.4
    )
    assert [event["operation"] for event in result["pause_events"]] == ["pause", "speed"]
    assert 0.35 < result["cut_ratio"] <= 0.4 and result["final_sec"] >= 60
    rejected = dict(start=30.2, end=33.8, operation="pause", retain_sec=options["retain_sec"])
    extra = processing.make_map(
        base["keep"], result["pause_events"] + [rejected], options["fade_sec"]
    )
    assert 1 - extra[-1]["final_end"] / base["original_sec"] > 0.4
