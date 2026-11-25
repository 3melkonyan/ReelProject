"""Board framing (G-24): find the classroom board and its tilt, for a crop to the board area.

Scope today: a dark (chalk) board on a light wall filmed from a tripod (the classroom of
lesson 4). Each sampled frame gives a quadrilateral: the largest dark region is taken and
each of its four edges is fitted with RANSAC, so the board's support posts and the lecturer's
legs (dark, sticking out of the board region) are outliers. The median over the sampled
frames removes the lecturer standing in front of the board. Whiteboards are not handled yet.

CLI: python -m reel.board VIDEO [--frames 20] [--debug out.jpg]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

WIDTH = 960            # analysis width; corners are returned normalised to 0..1
MIN_AREA = 0.15        # the board covers at least 15% of the frame
EDGE_SPAN = 0.8        # each edge is fitted on its central 80% (corners are often occluded)
LINE_TOL = 3.0         # px at WIDTH: a point within this distance supports a line
MIN_SUPPORT = 0.35     # share of edge points behind a line (the header rail splits the top edge)
AGREE = 0.02           # a frame agrees with the median when every corner is within 2%
MIN_FOUND = 0.5        # trust the result only if the board was found in half of the frames
MIN_AGREE = 0.6        # ... and 60% of those frames agree with the median
MAX_TILT = 15.0        # degrees; a steeper "board" is something else


def _line(u: np.ndarray, v: np.ndarray) -> tuple[float, float] | None:
    """v = a*u + b by RANSAC over point pairs (deterministic), refined on the inliers."""
    n = len(u)
    if n < 20:
        return None
    p, q = np.random.default_rng(0).integers(0, n, size=(2, 300))
    ok = u[p] != u[q]
    p, q = p[ok], q[ok]
    a = (v[q] - v[p]) / (u[q] - u[p])
    b = v[p] - a * u[p]
    support = (np.abs(v[None, :] - (a[:, None] * u[None, :] + b[:, None])) < LINE_TOL).sum(1)
    if not len(support) or support.max() < MIN_SUPPORT * n:
        return None
    k = int(support.argmax())
    inl = np.abs(v - (a[k] * u + b[k])) < LINE_TOL
    a1, b1 = np.polyfit(u[inl], v[inl], 1)
    return float(a1), float(b1)


def _mask(gray: np.ndarray) -> np.ndarray | None:
    h, w = gray.shape
    _, dark = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 0, 255,
                            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k = max(3, w // 60)                     # closes chalk writing and the rails between panels
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[i, cv2.CC_STAT_AREA] < MIN_AREA * w * h:
        return None
    return lab == i


def _span(idx: np.ndarray) -> np.ndarray:
    lo, hi = idx.min(), idx.max()
    cut = (hi - lo) * (1 - EDGE_SPAN) / 2
    return idx[(idx >= lo + cut) & (idx <= hi - cut)]


def detect_frame(gray: np.ndarray) -> np.ndarray | None:
    """Board corners TL, TR, BR, BL in pixels of `gray`, or None."""
    mask = _mask(gray)
    if mask is None:
        return None
    h, w = mask.shape
    cols = _span(np.where(mask.any(0))[0]).astype(float)
    rows = _span(np.where(mask.any(1))[0]).astype(float)
    c, r = cols.astype(int), rows.astype(int)
    top = _line(cols, mask[:, c].argmax(0).astype(float))
    bottom = _line(cols, (h - 1 - mask[::-1, c].argmax(0)).astype(float))
    left = _line(rows, mask[r, :].argmax(1).astype(float))
    right = _line(rows, (w - 1 - mask[r, ::-1].argmax(1)).astype(float))
    if None in (top, bottom, left, right):
        return None

    def meet(hz, vt):                       # y = a x + b  meets  x = c y + d
        (a, b), (cc, d) = hz, vt
        x = (cc * b + d) / (1 - cc * a)
        return x, a * x + b

    quad = np.array([meet(top, left), meet(top, right), meet(bottom, right), meet(bottom, left)])
    if not np.isfinite(quad).all() or abs(tilt_deg(quad)) > MAX_TILT \
            or cv2.contourArea(quad.astype(np.float32)) < MIN_AREA * w * h:
        return None
    return quad


def tilt_deg(corners: np.ndarray) -> float:
    """Camera roll, positive = clockwise on screen (the right side lower). Taken from the bottom
    edge and the mean of the two side edges. The top edge is left out: the rail above the board
    makes it jump between frames (lesson 4: top -0.9..+1.5 deg, bottom +0.05..0.25, sides
    +0.7/-0.95); averaging the sides also cancels their keystone (camera pitched up)."""
    tl, tr, br, bl = np.asarray(corners, float)
    bottom = np.degrees(np.arctan2(br[1] - bl[1], br[0] - bl[0]))
    left = np.degrees(np.arctan2(-(bl[0] - tl[0]), bl[1] - tl[1]))
    right = np.degrees(np.arctan2(-(br[0] - tr[0]), br[1] - tr[1]))
    return float((bottom + (left + right) / 2) / 2)


def combine(quads: list, frames: int, size: tuple[int, int]) -> dict:
    """Median board over the sampled frames (normalised quads) and whether to trust it."""
    out = {"frames": frames, "found": len(quads), "agree": 0.0, "corners": None, "tilt_deg": None}
    if quads:
        qs = np.array(quads, float)
        med = np.median(qs, axis=0)
        out["agree"] = round(float((np.abs(qs - med).max(axis=(1, 2)) < AGREE).mean()), 3)
        out["corners"] = [[round(float(x), 4), round(float(y), 4)] for x, y in med]
        out["tilt_deg"] = round(tilt_deg(med * size), 2)
    out["ok"] = bool(frames and out["found"] >= max(3, MIN_FOUND * frames) and out["agree"] >= MIN_AGREE)
    return out


def sample(video: str | Path, n: int = 20, width: int = WIDTH):
    """n frames spread over the lesson by fast seeks (~0.4 s each on 4K HEVC; a key-frame
    scan of a 100-min 4K lesson takes minutes)."""
    from reel.stages.transcribe import media_duration
    dur = media_duration(video)
    for i in range(n):
        t = dur * (0.03 + 0.94 * i / max(1, n - 1))
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           capture_output=True, timeout=60)
        h = len(r.stdout) // (width * 3)
        if r.returncode == 0 and h:
            yield np.frombuffer(r.stdout, np.uint8)[:width * h * 3].reshape(h, width, 3)


def detect(video: str | Path, *, n: int = 20, debug: str | Path | None = None) -> dict:
    quads, frames, shot, size = [], 0, None, None
    sampled = []
    for img in sample(video, n):
        frames += 1
        sampled.append(img)
        h, w = img.shape[:2]
        size = (w, h)
        q = detect_frame(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        if q is not None:
            quads.append(q / [w, h])
        if frames == 1 or frames % 7 == 0:
            shot = img
    out = combine(quads, frames, size)
    # Preserve the G-24 RANSAC result; colour-independent median/contour fallback handles
    # white/green boards, including a lecturer occluding individual frames.
    if sampled and (not out["ok"] or cv2.contourArea(np.float32(out["corners"])) > .92):
        from reel.framing import candidates, overlap
        background = np.median(np.stack(sampled), axis=0).astype(np.uint8)
        proposals = candidates(background)
        if proposals:
            best = proposals[0]
            agreement = sum(any(overlap(best["q"], p["q"]) > .65 for p in candidates(img)) for img in sampled) / len(sampled)
            if best["score"] >= .66 and agreement >= MIN_AGREE:
                out = {"frames": frames, "found": round(frames * agreement), "agree": round(agreement, 3),
                       "corners": best["q"].tolist(), "tilt_deg": round(tilt_deg(best["q"] * size), 2), "ok": True}
    if debug and shot is not None:
        pic = shot.copy()
        if out["corners"]:
            pts = (np.array(out["corners"]) * size).astype(np.int32)
            cv2.polylines(pic, [pts], True, (0, 0, 255), 2)
        cv2.imwrite(str(debug), pic)
    return out


ASPECT = 16 / 9        # output frame (YouTube); the board itself is wider (~2.3:1 in room 1)
MARGIN = 0.02          # around the board, share of its size


def _rot(points, angle_deg: float, centre) -> np.ndarray:
    """Rotate points clockwise on screen (image y axis points down) by angle_deg around centre."""
    a = np.radians(angle_deg)
    c, s = np.cos(a), np.sin(a)
    d = np.asarray(points, float) - centre
    return np.stack([d[:, 0] * c - d[:, 1] * s, d[:, 0] * s + d[:, 1] * c], 1) + centre


def plan(corners, width: int, height: int, *, aspect: float = ASPECT, margin: float = MARGIN) -> dict:
    """How to frame the board: rotate the whole frame by rotate_deg (clockwise, the ffmpeg
    `rotate` convention), then crop [x, y, w, h] of the same-size levelled frame. The crop has
    the output aspect, holds the board plus margin and never reaches the black rotated corners."""
    size = np.array([width, height], float)
    centre = size / 2
    tilt = tilt_deg(np.asarray(corners, float) * size)
    lev = _rot(np.asarray(corners, float) * size, -tilt, centre)
    (x0, y0), (x1, y1) = lev.min(0), lev.max(0)
    w = max((x1 - x0) * (1 + 2 * margin), (y1 - y0) * (1 + 2 * margin) * aspect)
    output_w = min(1920, width, int(height * aspect)) // 2 * 2
    w = min(max(w, output_w / 1.5), width, height * aspect)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for _ in range(200):
        h = w / aspect
        x, y = min(max(cx - w / 2, 0), width - w), min(max(cy - h / 2, 0), height - h)
        back = _rot([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], tilt, centre)
        if (back >= -0.5).all() and (back <= size + 0.5).all():
            break
        w *= 0.99
    h = w / aspect
    cut = max((max(0, x - x0) + max(0, x1 - x - w)) / (x1 - x0), (max(0, y - y0) + max(0, y1 - y - h)) / (y1 - y0))
    even = lambda v: int(round(v / 2) * 2)
    output_h = max(2, int(output_w / aspect) // 2 * 2)
    return {"size": [width, height], "output_size": [output_w, output_h], "max_upscale": 1.5,
            "cap_ok": output_w / max(2, even(w)) <= 1.5, "rotate_deg": round(-tilt, 3),
            "crop": [even(x), even(y), even(w), even(h)], "zoom": round(width / w, 3),
            "board_cut": round(float(cut), 3)}   # share of the board left out (steep tilt, board fills the frame)


def preview(img: np.ndarray, framing: dict, width: int = 640) -> np.ndarray:
    """What the published frame will look like (for the admin page and for checks)."""
    h, w = img.shape[:2]
    k = w / framing["size"][0]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -framing["rotate_deg"], 1.0)   # OpenCV: + = counter-clockwise
    x, y, cw, ch = (int(round(v * k)) for v in framing["crop"])
    out = cv2.warpAffine(img, m, (w, h))[y:y + ch, x:x + cw]
    return cv2.resize(out, (width, int(round(width * ch / cw / 2) * 2)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--debug")
    a = ap.parse_args()
    print(json.dumps(detect(a.video, n=a.frames, debug=a.debug), ensure_ascii=False))
