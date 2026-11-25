"""Board detection on synthetic frames (no video): a dark board on a light wall."""

import math

import cv2
import numpy as np

from reel.board import combine, detect_frame, tilt_deg

W, H = 960, 540


def frame(angle=0.0, legs=True, board=True):
    img = np.full((H, W), 200, np.uint8)  # light wall
    if board:
        cx, cy, a = W / 2, H / 2, math.radians(angle)
        pts = [
            (
                cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a),
                cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a),
            )
            for x, y in [(90, 130), (900, 130), (900, 470), (90, 470)]
        ]
        cv2.fillPoly(img, [np.int32(pts)], 35)
        cv2.putText(img, "def f(a, b):", (200, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 230, 3)  # chalk
        cv2.rectangle(img, (300, 0), (310, H), 35, -1)  # a support post through the board
        if legs:
            cv2.rectangle(img, (500, 440), (540, H), 30, -1)  # the lecturer's legs below it
        return img, np.array(pts)
    return img, None


def test_corners_and_tilt_with_posts_legs_and_chalk():
    for angle in (0.0, 3.0, -2.0):
        img, truth = frame(angle)
        q = detect_frame(img)
        assert q is not None and np.abs(q - truth).max() < 4, (angle, q)
        assert abs(tilt_deg(q) - angle) < 0.3  # + = right side lower


def test_no_board_or_a_small_dark_thing_is_not_a_board():
    assert detect_frame(frame(board=False)[0]) is None
    img = np.full((H, W), 200, np.uint8)
    cv2.rectangle(img, (100, 100), (250, 200), 35, -1)  # 3% of the frame
    assert detect_frame(img) is None


def test_combine_uses_the_median_and_reports_trust():
    good = np.array([[0.1, 0.24], [0.94, 0.22], [0.95, 0.87], [0.09, 0.87]])
    quads = [good + 0.001 * k for k in range(5)] + [good + 0.05]  # one bad frame
    r = combine(quads, 8, (W, H))
    assert r["ok"] and r["agree"] == round(5 / 6, 3) and abs(r["corners"][0][0] - 0.102) < 0.002
    assert not combine(quads[:2], 10, (W, H))["ok"]  # found in too few frames
    assert not combine([], 10, (W, H))["ok"]


def test_plan_levels_the_board_and_crops_16x9_inside_the_frame():
    from reel.board import _rot, plan

    for angle in (0.0, 3.0):
        _, truth = frame(angle)
        p = plan(truth / [W, H], W, H)
        x, y, w, h = p["crop"]
        assert abs(p["rotate_deg"] + angle) < 0.05 and abs(w / h - 16 / 9) < 0.01
        assert x >= 0 and x + w <= W and y >= 0 and y + h <= H
        back = _rot(
            [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], -p["rotate_deg"], (W / 2, H / 2)
        )
        assert (back >= -1).all() and (back <= [W + 1, H + 1]).all()  # no black corner
        lev = _rot(truth, p["rotate_deg"], (W / 2, H / 2))
        inside = lev[:, 0].min() >= x - 1 and lev[:, 0].max() <= x + w + 1
        if angle == 0:
            assert inside and p["board_cut"] == 0
        else:  # 3 deg on a board filling 84% of the width: a thin strip is cut
            assert 0 < p["board_cut"] < 0.05
