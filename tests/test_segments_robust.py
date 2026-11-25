"""A Whisper timestamp glitch must not stop a lesson (automation first): broken
sentences are dropped; when more than a few are broken, nothing is cut."""

import json

from reel.stages import segments as sg


def test_broken_timestamps_are_dropped_not_fatal():
    dropped = []
    rows = sg._sentences(
        [
            {"start": 0, "end": 10, "text": "ա"},
            {"start": 12, "end": 11, "text": "բ"},
            {"start": "x", "end": 3, "text": "գ"},
            {"start": 20, "end": 25, "text": "դ"},
            {"start": 26, "end": 26, "text": ""},
        ],
        30,
        dropped,
    )
    assert [r["id"] for r in rows] == [0, 3] and dropped == [1, 2]


def test_many_broken_timestamps_keep_the_whole_lesson(tmp_path, monkeypatch):
    segs = [{"start": i * 10, "end": i * 10 + 8, "text": "դաս"} for i in range(200)]
    segs += [{"start": 5, "end": 1, "text": "x"} for _ in range(10)]  # 10 > 2% of 210
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps({"segments": segs}), encoding="utf-8")
    monkeypatch.setattr(sg, "detect_silences", lambda *a, **k: [])
    monkeypatch.setattr(sg, "label_windows", lambda wins, *a, **k: ["lesson"] * len(wins))
    monkeypatch.setattr(sg, "_review", lambda *a, **k: {})
    r = sg.plan("v.mp4", "a.wav", transcript, 2000.0, {}, tmp_path / "out")
    assert (
        r["keep"] == [[0.0, 2000.0]] and r["cuts"] == [] and "broken timestamps" in r["auto_note"]
    )
