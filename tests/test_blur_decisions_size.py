"""Unsure faces kept visible are logged for the privacy guard at 5 Hz, not per output frame,
so the decisions sidecar of a 2-hour lesson stays small (the guard samples at 1 Hz, +-0.3 s)."""

from reel.blur import _BlurPolicy


def test_unsure_face_is_logged_five_times_per_second():
    policy = _BlurPolicy([{"t": 0.0, "box": [0, 0, 10, 10], "lecturer": True, "state": "lecturer"}])
    for frame in range(300):  # 10 s at 30 fps
        assert policy.state((500, 300, 80, 80), frame / 30) == "unsure"
    times = [entry["t"] for entry in policy.observed_unsure]
    assert 49 <= len(times) <= 52
    assert (
        max(b - a for a, b in zip(times, times[1:], strict=False)) <= 0.3
    )  # every guard sample finds one
