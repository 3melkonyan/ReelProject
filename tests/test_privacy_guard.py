"""Guard policy: judge against blur decisions, no video needed."""

from reel.stages.privacy import judge

DEC = {
    "fps": 25.0,
    "lecturer": [[10.0, 100, 100, 50, 60]],  # pass-1 lecturer box at t=10 s
    "pixelated": [[500, 400, 400, 470, 480]],
}  # frame 500 = t 20 s


def det(t, box, lecturer=False, sim=0.1):
    return {
        "t": t,
        "box": box,
        "lecturer": lecturer,
        "sim": sim,
        "state": "lecturer" if lecturer else "student",
    }


def test_lecturer_kept_by_blur_track_is_not_a_hit():
    r = judge([det(10.2, [102, 101, 50, 60])], DEC, 3600)  # within ±0.3 s, IoU > 0.3
    assert r["by_track"] == 1 and not r["hits"] and not r["warnings"] and r["reason"] is None


def test_face_inside_pixelated_region_is_already_blurred():
    r = judge([det(20.0, [410, 410, 50, 60])], DEC, 3600)
    assert r["pixelated"] == 1 and r["reason"] is None


def test_isolated_unexplained_face_is_warning_only():
    r = judge([det(30.0, [800, 100, 40, 50]), det(30.2, [800, 100, 40, 50])], DEC, 3600)
    assert len(r["warnings"]) == 2 and not r["hits"] and r["reason"] is None


def test_sustained_unexplained_face_stops():
    ds = [det(30.0 + 0.2 * k, [800, 100, 40, 50]) for k in range(3)]
    r = judge(ds, DEC, 3600)
    assert len(r["hits"]) == 3 and "visible after blur" in r["reason"]


def test_many_isolated_events_never_stop_but_are_flagged_for_review():
    # 1-2 frame cases must not stop the lesson, however many
    ds = [det(100.0 + 10 * k, [800, 100, 40, 50]) for k in range(5)]  # 5 events, 30 min video
    r = judge(ds, DEC, 1800)
    assert r["reason"] is None and not r["hits"] and len(r["warnings"]) == 5
    assert r["isolated"] == 5 and r["review"] is True and not any(e["stop"] for e in r["events"])
    two = [
        det(200.0, [800, 100, 40, 50]),
        det(200.2, [800, 100, 40, 50]),
    ]  # 2 frames in a row: still isolated
    assert judge(two, DEC, 1800)["reason"] is None


def test_without_decisions_only_sustained_student_stops():
    ds = [det(10.2 + 0.2 * i, [102, 101, 50, 60]) for i in range(3)]
    assert judge(ds[:1], None, 3600)["reason"] is None
    r = judge(ds, None, 3600)
    assert r["reason"] and len(r["hits"]) == 3


def test_recognised_lecturer_counts_and_never_stops():
    r = judge([det(5.0, [0, 0, 50, 60], lecturer=True, sim=0.8)], DEC, 3600)
    assert r["lecturer"] == 1 and r["reason"] is None


def test_stopping_events_are_reported_as_hits():
    # The report must not say "0 foreign faces" while the job stops
    ds = [det(30.0 + 0.2 * k, [800, 100, 40, 50]) for k in range(3)] + [
        det(500.0, [800, 100, 40, 50])
    ]
    r = judge(ds, DEC, 1800)
    assert r["reason"] and len(r["hits"]) == 3 and len(r["warnings"]) == 1
    assert [e["stop"] for e in r["events"]] == [True, False]
