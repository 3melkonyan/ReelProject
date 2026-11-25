from reel import timeline
from reel.stages.segments import DEFAULTS, offtopic_cuts, windows


def test_timeline_mapping():
    keep = timeline.subtract((0, 100), [(0, 10), (40, 50)])
    assert keep == [(10, 40), (50, 100)]
    assert timeline.to_final(10, keep) == 0
    assert timeline.to_final(45, keep) == 30  # inside a cut -> next kept start
    assert timeline.to_final(60, keep) == 40
    assert timeline.duration(keep) == 80


def test_offtopic_rules():
    segs = [{"start": 0, "end": 600, "text": "x"}]
    wins = windows(segs, 600, 60)  # 10 windows
    L = [
        "offtopic",
        "lesson",
        "offtopic",
        "lesson",
        "offtopic",
        "offtopic",
        "offtopic",
        "lesson",
        "offtopic",
        "offtopic",
    ]
    cuts = offtopic_cuts(wins, L, DEFAULTS)
    kinds = [(s, e, k) for s, e, k in cuts]
    assert (0, 60, "before_class") in kinds  # edge run cut even if short
    assert all(not (s == 120 and e == 180) for s, e, _ in kinds)  # short mid offtopic kept (Ո3)
    assert (240, 420, "break") in kinds  # 3 min run = break
    assert (480, 600, "after_class") in kinds


def test_unclear_never_bridges_offtopic_runs():
    segs = [{"start": 0, "end": 600, "text": "x"}]
    wins = windows(segs, 600, 60)
    L = [
        "lesson",
        "lesson",
        "offtopic",
        "silence",
        "unclear",
        "offtopic",
        "lesson",
        "lesson",
        "silence",
        "lesson",
    ]
    cuts = offtopic_cuts(wins, L, DEFAULTS)
    # uncertain text cannot be promoted to outside, even between runs.
    assert (120, 360, "break") not in cuts
    # a lone silence between lesson windows is NOT cut by the LLM rule
    assert all(s != 480 for s, _, _ in cuts)


def test_window_size_must_be_positive():
    # window_sec 0 used to loop forever
    import pytest

    from reel.pipeline import StopJob

    for bad in (0, -60, float("nan"), float("inf")):
        with pytest.raises(StopJob):
            windows([], 600, bad)


def test_measured_silence_overrides_hallucinated_text():
    # real run 2026-11-18: Whisper wrote "Եստես այդ այդ..." over silent minutes -> 'unclear' labels
    from reel.stages.segments import blank_silent_windows

    wins = [
        {"start": 0, "end": 60, "text": "Եստես այդ այդ այդ"},
        {"start": 60, "end": 120, "text": "real lesson words"},
        {"start": 120, "end": 180, "text": "Եստես այդ այդ"},
    ]
    n = blank_silent_windows(wins, [(0.0, 55.0), (125.0, 180.0)])  # 92% and 92% silent
    assert n == 2 and wins[0]["text"] == "" and wins[2]["text"] == "" and wins[1]["text"]
    assert blank_silent_windows(wins, [(60.0, 90.0)]) == 0  # 50% silent: keep text


def test_bounded_silence_is_only_an_offtopic_proposal():
    wins = windows([], 360, 60)
    labels = ["lesson", "offtopic", "silence", "offtopic", "offtopic", "lesson"]
    assert (60, 300, "break") in offtopic_cuts(wins, labels, DEFAULTS)
