"""Fail-open sentence evidence, precise boundaries and consumer contracts."""

import json

import pytest

from reel import timeline
from reel.pipeline import StopJob
from reel.stages import segments as sg


def row(start, end, text):
    return dict(start=start, end=end, text=text)


def decision(*, protected=(), outside=(), first=None, last=None, closing=None, cuts=()):
    return dict(
        confidence="high",
        protected_ids=list(protected),
        outside_ids=list(outside),
        first_lesson_id=first,
        last_lesson_id=last,
        closing_id=closing,
        cuts=list(cuts),
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    def execute(
        rows,
        total,
        *,
        start=None,
        end=None,
        middle=None,
        labels=None,
        quiet=(),
        proposals=(),
        config=None,
    ):
        path = tmp_path / "transcript.json"
        path.write_text(json.dumps({"segments": rows}))
        calls = []

        def chat(system, user, **kwargs):
            calls.append(user)
            if user.startswith("task=start"):
                return start or {}
            if user.startswith("task=end"):
                return end or {}
            if user.startswith("task=middle"):
                return middle or {}
            raise AssertionError("Unexpected LLM call")

        monkeypatch.setattr(sg.llm, "chat_json", chat)
        monkeypatch.setattr(sg, "label_windows", lambda wins: labels or ["lesson"] * len(wins))
        monkeypatch.setattr(sg, "detect_silences", lambda *a: list(quiet))
        monkeypatch.setattr(sg, "silence_cuts", lambda *a: list(proposals))
        result = sg.plan(
            "unused-video",
            "unused-audio",
            path,
            total,
            {"min_final_sec": 0, **(config or {})},
            tmp_path / "out",
        )
        assert json.loads((tmp_path / "out/segments.json").read_text()) == result
        assert timeline.duration([tuple(p) for p in result["keep"]]) == pytest.approx(
            result["final_sec"], abs=0.051
        )
        applied = timeline.subtract((0, total), [tuple(p) for p in result["keep"]])
        assert applied == [(c["start"], c["end"]) for c in result["cuts"]]
        for a, b in applied:
            assert sg._safe_boundary(a, rows) and sg._safe_boundary(b, rows)
        execute.calls = calls
        return result

    return execute


def test_prelude_starts_at_introductory_greeting_sentence(run):
    rows = [
        row(0, 15, "Սպասենք, պրոյեկտորը միացնենք"),
        row(20, 30, "Բարև ձեզ, այսօր կանցնենք նոր թեմա"),
        row(30, 90, "Բացատրություն"),
    ]
    r = run(rows, 100, start=decision(first=1, protected=[1, 2], outside=[0]))
    assert r["keep"] == [[18.5, 100]]
    assert r["cuts"] == [dict(start=0.0, end=18.5, why="before_class")]


def test_closing_kept_and_tail_cut_immediately_after_it(run):
    rows = [
        row(0, 80, "Բացատրություն"),
        row(80, 90, "Այսօրվա համար այսքանը, մինչ հաջորդ դաս"),
        row(95, 120, "Երեկ ֆուտբոլ նայեցի՞ք"),
    ]
    r = run(rows, 130, end=decision(last=1, closing=1, protected=[0, 1], outside=[2]))
    assert r["keep"] == [[0, 91.5]]
    assert r["cuts"] == [dict(start=91.5, end=130, why="after_class")]


def test_subject_qa_after_farewell_extends_end_even_if_last_id_is_stale(run):
    rows = [
        row(0, 50, "Բացատրություն"),
        row(50, 60, "Այսօր այսքանը, մինչ հաջորդ դաս"),
        row(65, 75, "Իսկ function-ի return-ը ինչ է"),
        row(75, 90, "Այն վերադարձնում է արդյունքը"),
        row(95, 110, "Գնանք սուրճ խմենք"),
    ]
    r = run(rows, 120, end=decision(last=1, closing=1, protected=[0, 1, 2, 3], outside=[4]))
    assert r["keep"] == [[0, 91.5]] and r["edges"]["end"]["sentence_id"] == 3


def test_middle_topic_ending_is_not_class_ending(run):
    rows = [
        row(0, 50, "Բացատրություն"),
        row(50, 60, "Այս թեման այսքանն էր"),
        row(65, 150, "Հիմա հաջորդ թեման բացատրեմ"),
    ]
    r = run(rows, 160, end=decision(last=1, closing=1, protected=[0, 1], outside=[2]))
    assert r["keep"] == [[0, 160]] and not r["cuts"]


def test_explicit_break_cuts_silent_gap_preserving_announcement(run):
    rows = [
        row(0, 15, "Բացատրություն"),
        row(15, 20, "Տասնհինգ րոպե ընդմիջում ենք անում"),
        row(260, 280, "Շարունակենք բացատրությունը"),
    ]
    review = decision(
        protected=[0, 1, 2],
        cuts=[dict(after_id=1, before_id=2, kind="break", confidence="high", break_anchor_id=1)],
    )
    r = run(
        rows,
        360,
        middle=review,
        proposals=[(20, 260, "silence(activity=0.1)")],
        config={"max_cut_ratio": 0.9},
    )
    assert r["cuts"] == [dict(start=21.5, end=258.5, why="break")]


def test_long_unrelated_chatter_uses_sentence_edges_not_window_edges(run):
    rows = [
        row(0, 30, "Բացատրություն"),
        row(30, 120, "Երեկ ֆուտբոլ նայեցի"),
        row(120, 240, "Սննդի մասին երկար զրույց"),
        row(240, 400, "Վերադառնանք դասին"),
    ]
    review = decision(
        protected=[0, 3],
        outside=[1, 2],
        cuts=[dict(after_id=0, before_id=3, kind="offtopic", confidence="high")],
    )
    r = run(
        rows,
        400,
        middle=review,
        labels=["lesson", "offtopic", "offtopic", "offtopic", "lesson", "lesson", "lesson"],
        config={"max_cut_ratio": 0.6},
    )
    assert r["cuts"] == [dict(start=30, end=240, why="offtopic")]


@pytest.mark.parametrize(
    "middle",
    [
        {},
        {"confidence": "unclear"},
        decision(
            protected=[0, 3],
            outside=[1],
            cuts=[dict(after_id=0, before_id=3, kind="offtopic", confidence="high")],
        ),
    ],
)
def test_unknown_or_partly_unknown_block_is_kept(run, middle):
    rows = [
        row(0, 30, "Բացատրություն"),
        row(30, 120, "Անորոշ խոսք"),
        row(120, 240, "Անորոշ խոսք"),
        row(240, 400, "Շարունակենք"),
    ]
    r = run(rows, 400, middle=middle, proposals=[(30, 240, "break")])
    assert not r["cuts"]


def test_static_silence_without_explicit_break_evidence_is_kept(run):
    rows = [row(0, 20, "Գրեք խնդիրը"), row(260, 300, "Այժմ լուծենք")]
    review = decision(
        protected=[0, 1], cuts=[dict(after_id=0, before_id=1, kind="break", confidence="high")]
    )
    assert not run(rows, 400, middle=review, proposals=[(20, 260, "silence(activity=0)")])["cuts"]


def test_short_thinking_pause_is_never_proposed_for_cut(run):
    rows = [row(0, 20, "Մտածեք"), row(48, 90, "Լուծումը սա է")]
    r = run(rows, 100, proposals=[(20, 48, "silence(activity=0)")])
    assert not r["cuts"] and not any(c.startswith("task=middle") for c in run.calls)


def test_contiguous_speech_boundaries_do_not_put_padding_inside_words(run):
    rows = [
        row(0, 20, "Սպասենք"),
        row(20, 50, "Բարև, այսօր անցնենք թեման"),
        row(50, 60, "Այսօրվա համար այսքանը"),
        row(60, 90, "Կողմնակի զրույց"),
    ]
    r = run(
        rows,
        100,
        start=decision(first=1, protected=[1, 2], outside=[0, 3]),
        end=decision(last=2, closing=2, protected=[1, 2], outside=[0, 3]),
        config={"max_cut_ratio": 0.8},
    )
    assert r["keep"] == [[20, 60]]


def test_ambiguous_overlap_at_boundary_keeps_edge(run):
    rows = [row(0, 25, "Սպասենք"), row(20, 60, "Բարև, այսօր նոր թեմա")]
    r = run(rows, 100, start=decision(first=1, protected=[1], outside=[0]))
    assert r["keep"] == [[0, 100]]


def test_confirmed_edges_still_obey_total_max_cut_ratio(run):
    rows = [
        row(0, 200, "Սպասենք"),
        row(200, 250, "Բարև, այսօր նոր թեմա"),
        row(250, 300, "Այսօրվա համար այսքանը"),
        row(300, 1000, "Կողմնակի զրույց"),
    ]
    r = run(
        rows,
        1000,
        start=decision(first=1, protected=[1, 2], outside=[0, 3]),
        end=decision(last=2, closing=2, protected=[1, 2], outside=[0, 3]),
        config={"max_cut_ratio": 0.01},
    )
    assert r["keep"] == [[0, 1000]] and r["cut_ratio"] == 0 and r["interior_cut_ratio"] == 0
    assert r["cuts"] == [] and r["no_cut_code"] == "over_max_ratio"
    assert r["no_cut_details"] == {"percent": 90.0, "limit": 1.0}


@pytest.mark.parametrize(
    "text",
    [
        "Տնային աշխատանքը սա է",
        "Քննության պայմանները",
        "function-ի բացատրություն",
        "կազմակերպչական հայտարարություն",
    ],
)
def test_protected_material_cannot_be_cut_even_if_model_says_outside(run, text):
    rows = [row(0, 20, "Բացատրություն"), row(20, 240, text), row(240, 400, "Շարունակենք")]
    review = decision(
        protected=[0, 2],
        outside=[1],
        cuts=[dict(after_id=0, before_id=2, kind="offtopic", confidence="high")],
    )
    assert not run(rows, 400, middle=review, proposals=[(20, 240, "break")])["cuts"]


def test_out_of_range_and_boolean_sentence_ids_are_not_accepted(run):
    rows = [row(0, 20, "Սպասենք"), row(20, 80, "Բացատրություն")]
    for ident in (True, 999, -1, "1"):
        assert not run(rows, 100, start=decision(first=ident, protected=[1], outside=[0]))["cuts"]


def test_mid_model_failure_keeps_candidate(monkeypatch):
    def unavailable(*a, **k):
        raise sg.llm.LLMError("offline")

    monkeypatch.setattr(sg.llm, "chat_json", unavailable)
    assert sg._review(sg.EDGE_SYSTEM, "start", [dict(id=0, **row(0, 20, "Սպասենք"))])[
        "reason"
    ].endswith("kept")


def test_long_context_never_silently_drops_tail_qa(monkeypatch):
    monkeypatch.setattr(
        sg.llm, "chat_json", lambda *a, **k: pytest.fail("Oversized context must stay intact")
    )
    assert (
        "oversized"
        in sg._review(sg.EDGE_SYSTEM, "end", [dict(id=0, **row(0, 20, "ա" * 25000))])["reason"]
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_invalid_total_rejected_before_signal_work(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(sg, "detect_silences", lambda *a: pytest.fail("Invalid duration"))
    with pytest.raises(StopJob):
        sg.plan("v", "a", tmp_path / "absent.json", bad, None, tmp_path / "out")


def test_empty_zero_length_asr_marker_is_ignored_without_changing_sentence_ids():
    rows = sg._sentences(
        [row(0, 20, "Բացատրություն"), row(20, 20, ""), row(20, 30, "Շարունակենք")], 30
    )
    assert [r["id"] for r in rows] == [0, 2]


def test_unrelated_speech_does_not_absorb_adjacent_thinking_silence(run):
    rows = [
        row(0, 20, "Բացատրություն"),
        row(100, 300, "Կողմնակի երկար զրույց"),
        row(360, 500, "Շարունակենք"),
    ]
    review = decision(
        protected=[0, 2],
        outside=[1],
        cuts=[dict(after_id=0, before_id=2, kind="offtopic", confidence="high")],
    )
    r = run(
        rows, 500, middle=review, proposals=[(100, 300, "break")], config={"max_cut_ratio": 0.5}
    )
    assert r["cuts"] == [dict(start=100, end=300, why="offtopic")]


def test_explicit_last_closing_id_survives_model_omission_from_protected_list(run):
    rows = [
        row(0, 80, "Բացատրություն"),
        row(80, 90, "Այսօրվա համար այսքանը"),
        row(95, 120, "Կողմնակի զրույց"),
    ]
    r = run(rows, 130, end=decision(last=1, closing=1, protected=[0], outside=[2]))
    assert r["keep"] == [[0, 91.5]] and r["edges"]["end"]["confirmed"]


def test_bad_window_numbering_retries_with_exact_id_schema(monkeypatch):
    calls = []

    def chat(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"labels": [{"i": 0, "label": "lesson"}, {"i": 1, "label": "lesson"}]}
        if len(calls) == 2:
            return {"labels": [{"i": 0, "label": "offtopic"}]}
        assert kwargs["schema"]["properties"]["labels"]["items"]["properties"]["i"]["enum"] == [2]
        return {"labels": [{"i": 2, "label": "lesson"}]}

    monkeypatch.setattr(sg.llm, "chat_json", chat)
    assert (
        sg.label_windows([dict(start=i, end=i + 1, text="text") for i in range(3)], batch=2)
        == ["lesson"] * 3
    )


def test_unresolved_window_ids_are_unclear_not_renumbered(monkeypatch):
    monkeypatch.setattr(
        sg.llm, "chat_json", lambda *a, **k: {"labels": [{"i": 999, "label": "offtopic"}]}
    )
    assert sg.label_windows([dict(start=0, end=60, text="text")]) == ["unclear"]


def test_overlapping_negative_span_cannot_swallow_whole_protected_sentence(run):
    rows = [
        row(0, 20, "Բացատրություն"),
        row(20, 400, "Կողմնակի զրույց"),
        row(240, 300, "Պահվող խոսք"),
        row(400, 600, "Շարունակենք"),
    ]
    review = decision(
        protected=[0, 2, 3],
        outside=[1],
        cuts=[dict(after_id=0, before_id=2, kind="offtopic", confidence="high")],
    )
    r = run(rows, 600, middle=review, proposals=[(20, 400, "break")], config={"max_cut_ratio": 0.8})
    assert not r["cuts"]
