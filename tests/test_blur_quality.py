"""Only confident students are blurred, retroactively."""

import json

import numpy as np
import pytest

from reel import blur
from reel.stages import privacy


def face(x=20, height=100, yaw=0, score=0.99):
    # YuNet eyes, nose, mouth corners, detector confidence.
    return np.array(
        [
            x,
            20,
            100,
            height,
            x + 25,
            45,
            x + 65,
            45,
            x + 45 + yaw * 40,
            65,
            x + 30,
            90,
            x + 60,
            90,
            score,
        ],
        dtype=np.float32,
    )


def record(track=0, t=0, sim=0.1, clear=True, lecturer=False, box=None):
    return dict(
        track=track, t=t, sim=sim, clear=clear, lecturer=lecturer, box=box or [20, 20, 100, 100]
    )


def resolve(sims, clear=None):
    items = [
        record(t=i / 5, sim=s, clear=(clear[i] if clear else True)) for i, s in enumerate(sims)
    ]
    blur._classify_analysis(items)
    return items


def test_lecturer_turn_gap_return_never_blurred():
    tracker = blur._Tracker()
    items = []
    for t, box, sim in [
        (0, (20, 20, 100, 100), 0.15),
        (0.2, (22, 20, 100, 100), 0.6),
        (0.4, (90, 20, 60, 100), 0.1),
        (1.9, (110, 20, 65, 100), 0.05),
    ]:
        known = tracker.update([box], [sim >= blur.COSINE_MATCH], t)[0]
        items.append(record(t=t, sim=sim, lecturer=known, box=list(box)))
        items[-1]["_object"] = tracker.current[0]
    assert len({id(a.pop("_object")) for a in items}) == 1
    blur._classify_analysis(items)
    policy = blur._BlurPolicy(items)
    for a in items:
        assert a["state"] == "lecturer"
        assert policy.state(tuple(a["box"]), a["t"]) == "lecturer"


def test_student_blurred_from_first_sample_and_margin_hold_preserved():
    items = resolve([0.10, 0.15, 0.20])
    items.append(record(track=1, t=0, lecturer=True, sim=0.7, box=[250, 20, 100, 100]))
    policy = blur._BlurPolicy(items)

    class ID:
        refs = [True]

        def faces(self, image):
            return [face()]

    for a in items[:3]:
        boxes, known, unknown = blur._detections(
            ID(), np.zeros((200, 400, 3), np.uint8), t=a["t"], lecturer_boxes=policy
        )
        assert boxes == [(5, 5, 135, 135)] and known == 0 and unknown == 1
    from collections import deque

    history = deque()
    assert blur._retained(history, 0, boxes, 3) == boxes
    assert blur._retained(history, 3, [], 3) == boxes
    assert blur._retained(history, 4, [], 3) == []


@pytest.mark.parametrize("sample", [face(height=40), face(yaw=0.4), face(score=0.8), np.zeros(15)])
def test_small_profile_low_score_and_degenerate_faces_unsure(sample):
    quality = blur._quality(sample)
    assert not quality["clear"]
    items = resolve([0.1, 0.1, 0.1], [quality["clear"]] * 3)
    assert {a["state"] for a in items} == {"unsure"}


def test_frontalness_is_roll_independent():
    f = face()
    assert blur._quality(f)["clear"]
    points = f[4:14].reshape(5, 2)
    angle = 0.4
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    f[4:14] = (points @ rotation.T).ravel()
    assert blur._quality(f)["clear"]


@pytest.mark.parametrize(
    "sims",
    [
        [0.1, 0.1],
        [0.1, 0.1, 0.1, 0.25],
        [0.1, 0.1, 0.1, 0.3],
        [0.1, 0.1, 0.1, None],
        [0.1, 0.1, 0.1, float("nan")],
    ],
)
def test_missing_invalid_or_intermediate_evidence_vetoes_student(sims):
    assert {a["state"] for a in resolve(sims)} == {"unsure"}


def test_single_match_overrides_every_uncertainty_retroactively():
    assert {a["state"] for a in resolve([None, 0.1, 0.3, blur.COSINE_MATCH])} == {"lecturer"}


def test_profile_samples_do_not_count_as_three_clear_samples():
    assert resolve([0.1, 0.1, 0.1, 0.1], [True, False, False, True])[0]["state"] == "unsure"
    assert resolve([0.1, 0.1, 0.1, 0.1], [True, False, True, True])[0]["state"] == "student"


def test_relabel_flags_override_previously_student_track():
    items = resolve([0.1, 0.1, 0.1])
    items[1]["lecturer"] = True
    blur._classify_analysis(items)
    assert all(a["state"] == "lecturer" for a in items)


def test_tracker_expires_large_gap_rejects_scale_change_and_is_one_to_one():
    tr = blur._Tracker()
    tr.update([(0, 0, 100, 100)], [True], 0)
    assert tr.update([(0, 0, 100, 100), (2, 0, 100, 100)], [False, False], 1.5).count(True) == 1
    assert tr.update([(0, 0, 100, 100)], [False], 3.6) == [False]
    tr = blur._Tracker()
    tr.update([(0, 0, 100, 100)], [True], 0)
    assert tr.update([(10, 10, 10, 10)], [False], 0.2) == [False]


def test_unknown_pass_two_face_is_kept_and_logged(tmp_path):
    items = [record(lecturer=True, sim=0.8, box=[250, 20, 100, 100])]
    policy = blur._BlurPolicy(items)

    class ID:
        refs = [True]

        def faces(self, image):
            return [face()]

    boxes, _, _ = blur._detections(
        ID(), np.zeros((200, 400, 3), np.uint8), t=0, lecturer_boxes=policy
    )
    assert boxes == [] and len(policy.observed_unsure) == 1
    path = tmp_path / "decisions.json"
    blur._write_decisions(path, 25, items + policy.observed_unsure, [])
    dec = json.loads(path.read_text())
    assert dec["unsure"] and dec["policy"] == "confident_students"
    ds = [dict(record(t=t), state="student") for t in [0, 0.2, 0.4]]
    # First is explicitly kept, the latter two remain isolated student warnings.
    assert privacy.judge(ds, dec, 10)["unsure"] >= 1


def test_no_lecturer_preserves_blur_every_face_fallback():
    items = resolve([None, 0.2, 0.3])
    blur._classify_analysis(items, fallback=True)
    assert all(a["state"] == "student" for a in items)
    assert blur._BlurPolicy(items).state((800, 0, 100, 100), 0) == "student"


def test_unsure_detections_never_warn_or_stop():
    items = [dict(record(t=i / 5), state="unsure") for i in range(20)]
    dec = {"fps": 25, "lecturer": [], "unsure": [], "pixelated": [], "policy": "confident_students"}
    r = privacy.judge(items, dec, 10)
    assert r["unsure"] == 20 and not r["reason"] and not r["warnings"] and not r["hits"]
    assert not r["events"] and not r["review"]


def test_decision_unsure_explains_even_guard_student():
    ds = [dict(record(t=i / 5), state="student") for i in range(5)]
    dec = {
        "fps": 25,
        "lecturer": [],
        "unsure": [[a["t"], *a["box"]] for a in ds],
        "pixelated": [],
        "policy": "confident_students",
    }
    r = privacy.judge(ds, dec, 10)
    assert r["unsure"] == 5 and not r["reason"] and not r["warnings"]


def test_sustained_student_still_stops_and_fallback_still_stops():
    ds = [dict(record(t=i / 5), state="student") for i in range(3)]
    dec = {"fps": 25, "lecturer": [], "unsure": [], "pixelated": [], "policy": "confident_students"}
    assert privacy.judge(ds, dec, 10)["reason"]
    dec["policy"] = "fallback"
    for a in ds:
        a["state"] = "unsure"
    assert privacy.judge(ds, dec, 10)["reason"]


def test_smoke():
    blur._unit_smoke()


def test_actual_two_pass_encode_and_guard_report(tmp_path, monkeypatch):
    import subprocess

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    sidecar = tmp_path / "decisions.json"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=400x200:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=30,
    )

    class ID:
        refs = [True]

        def __init__(self, refs):
            pass

        def faces(self, image):
            return [face(x=230), face(x=20), face(x=140, height=40)]

        def feature(self, image, f):
            return float(f[0])

        def best_match(self, f):
            return 0.8 if f == 230 else 0.1

    monkeypatch.setattr(blur, "FaceID", ID)
    applied = []
    original = blur._pixelate

    def pixelate(frame, box):
        applied.append(box)
        original(frame, box)

    monkeypatch.setattr(blur, "_pixelate", pixelate)
    report = blur.blur_faces(str(source), str(output), ["fake"], decisions_path=str(sidecar))
    assert output.stat().st_size > 0 and report["frames"] == 10
    assert report["student_tracks"] == report["unsure_tracks"] == 1
    assert len(applied) == 10 and set(applied) == {(5, 5, 135, 135)}
    decisions = json.loads(sidecar.read_text())
    assert len(decisions["pixelated"]) == 10 and decisions["unsure"]
    monkeypatch.setattr(
        blur,
        "analyze",
        lambda *a, **k: [
            dict(record(t=i / 5, box=[140, 20, 100, 40]), state="unsure") for i in range(5)
        ],
    )
    # A real scan writes only an unsure count, no crop/warning/StopJob.
    scan = privacy.scan(output, ["fake"], tmp_path / "guard", decisions=sidecar)
    assert scan["unsure_faces"] == 5 and scan["warnings"] == scan["other_faces"] == 0
    saved = json.loads((tmp_path / "guard/privacy.json").read_text())
    assert saved["stopped"] is None and not saved["warning_faces"]
    assert not list((tmp_path / "guard").glob("face_*.jpg"))


def test_held_student_box_does_not_cover_returning_lecturer(tmp_path, monkeypatch):
    import subprocess

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=400x200:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=30,
    )

    class ID:
        refs = [True]

        def __init__(self, refs):
            self.n = 0

        def faces(self, image):
            self.n += 1
            return [face(x=20)] if self.n == 1 else [face(x=80)]

    monkeypatch.setattr(blur, "FaceID", ID)
    items = [record(track=0, t=t) for t in [0, 0.2, 0.4]]
    items += [
        record(track=1, t=t, box=[80, 20, 100, 100], lecturer=True, sim=0.8)
        for t in [0.4, 0.6, 0.8]
    ]
    applied = []
    monkeypatch.setattr(blur, "_pixelate", lambda f, b: applied.append(b))
    blur.blur_faces(str(source), str(output), ["fake"], analysis=items)
    # At t=.1 the lecturer exemption already applies (nearest pass-1 anchor=.4).
    assert len(applied) == 1


def test_explicit_student_without_sidecar_requires_sustained_visibility():
    ds = [dict(record(t=i / 5), state="student") for i in range(3)]
    assert privacy.judge(ds[:1], None, 10)["reason"] is None
    assert privacy.judge(ds, None, 10)["reason"]


def test_legacy_sidecar_honours_new_unsure_state_when_lecturer_was_found():
    d = dict(record(), state="unsure")
    dec = {"fps": 25, "lecturer": [[10, 250, 20, 100, 100]], "pixelated": []}
    r = privacy.judge([d], dec, 20)
    assert r["unsure"] == 1 and not r["warnings"] and not r["reason"]


def test_missing_identity_evidence_is_unsure_not_student():
    ds = [dict(t=i / 5, box=[20, 20, 100, 100], lecturer=False, sim=0.1) for i in range(5)]
    dec = {
        "fps": 25,
        "lecturer": [[10, 250, 20, 100, 100]],
        "pixelated": [],
        "policy": "confident_students",
    }
    r = privacy.judge(ds, dec, 20)
    assert r["unsure"] == 5 and not r["reason"] and not r["warnings"]


def test_raw_positive_evidence_disables_no_lecturer_fallback_with_stale_flags():
    items = [record(track=0, sim=0.6, lecturer=False), record(track=1, sim=None, clear=False)]
    blur._classify_analysis(items, fallback=True)
    assert [a["state"] for a in items] == ["lecturer", "unsure"]


def test_mixed_size_student_track_is_still_blurred():
    """Owner privacy rule: a student whose face is sometimes < 24 px must not become
    a visible "unsure" track just because the small samples carry no identity."""
    items = [record(t=i / 5, sim=s) for i, s in enumerate([0.1, 0.05, 0.12, 0.08])]
    items += [record(t=1 + i / 5, sim=None, clear=False, box=[20, 20, 20, 20]) for i in range(3)]
    for a in items[4:]:
        a["small"] = True
    blur._classify_analysis(items)
    assert {a["state"] for a in items} == {"student"}


def test_small_samples_do_not_hide_ambiguous_or_lecturer_evidence():
    ambiguous = [record(t=i / 5, sim=s) for i, s in enumerate([0.1, 0.3, 0.12, 0.08])]
    ambiguous += [record(t=1, sim=None, clear=False, box=[20, 20, 20, 20])]
    blur._classify_analysis(ambiguous)
    assert {a["state"] for a in ambiguous} == {"unsure"}
    lecturer = [record(t=0, sim=0.7), record(t=0.2, sim=None, clear=False, box=[20, 20, 20, 20])]
    blur._classify_analysis(lecturer)
    assert {a["state"] for a in lecturer} == {"lecturer"}
    tiny = [record(t=i / 5, sim=None, clear=False, box=[18, 18, 18, 18]) for i in range(3)]
    blur._classify_analysis(tiny)
    assert {a["state"] for a in tiny} == {"student"}
