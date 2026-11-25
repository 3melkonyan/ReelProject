"""C-6: privacy guard (PRD Ո1) — the last check before anything is published.

Re-detects faces on the BLURRED video and judges them against the blur stage's own
decisions (G-9.6), see judge(). FaceID (YuNet + SFace) is shared with reel/blur.py.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from reel.pipeline import StopJob

ROOT = Path(__file__).resolve().parents[2]
DET_MODEL = ROOT / "models" / "face_detection_yunet_2023mar.onnx"
REC_MODEL = ROOT / "models" / "face_recognition_sface_2021dec.onnx"
COSINE_MATCH = 0.363      # SFace recommended threshold for "same person"
DET_SCORE = 0.7
WIDTH = 1920              # G-42: preserve >=16px rendered faces in the guard too


class FaceID:
    def __init__(self, refs_dirs: list[Path]):
        self.det = cv2.FaceDetectorYN.create(str(DET_MODEL), "", (320, 320), DET_SCORE)
        self.rec = cv2.FaceRecognizerSF.create(str(REC_MODEL), "")
        self.refs = [f for d in refs_dirs for f in self._load_refs(d)]

    def faces(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        return faces if faces is not None else np.empty((0, 15), np.float32)

    def feature(self, img, face) -> np.ndarray:
        return self.rec.feature(self.rec.alignCrop(img, face))

    def best_match(self, feat) -> float:
        return max((self.rec.match(feat, r, cv2.FaceRecognizerSF_FR_COSINE) for r in self.refs),
                   default=0.0)

    def _load_refs(self, d: Path) -> list[np.ndarray]:
        feats = []
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            cache = p.with_suffix(".npz")
            if cache.exists():
                try:
                    with np.load(cache, allow_pickle=False) as stored:
                        feat = np.array(stored["feature"], dtype=np.float32, copy=True)
                        digest = str(stored["image_sha256"].item())
                    if feat.shape != (1, 128) or not np.isfinite(feat).all() or digest != hashlib.sha256(p.read_bytes()).hexdigest():
                        raise ValueError("reference cache does not match its image")
                    feats.append(feat)
                except (OSError, ValueError, KeyError) as exc:
                    raise RuntimeError(f"Invalid reference descriptor {cache.name}; regenerate refs") from exc
            # Keep crop-derived descriptors too, so existing matches are retained.
            img = cv2.imread(str(p))
            if img is None:
                continue
            fs = self.faces(img)
            if len(fs) == 0:
                continue
            biggest = max(fs, key=lambda f: f[2] * f[3])
            feats.append(self.feature(img, biggest))
        return feats


def _frames(video: Path, fps: float, width: int):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, check=True).stdout.strip().split(",")
    w0, h0 = int(probe[0]), int(probe[1])
    w = min(width, w0)
    h = int(round(h0 * w / w0 / 2) * 2)
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-hwaccel",
                             "videotoolbox", "-i", str(video), "-vf", f"fps={fps},scale={w}:{h}",
                             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    i = 0
    try:
        while (buf := proc.stdout.read(size)) and len(buf) == size:
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


# --- guard policy (G-9.6) ----------------------------------------------------------
# G-27: unsure faces are counted only, without warnings or stops. Confident
# students still use the sustained-event policy below. No-lecturer fallback
# retains the previous blur-everyone enforcement.
# Blur decisions explain lecturer tracks, deliberately visible unsure faces,
# and regions already pixelated. Only remaining confident student detections
# form events. Isolated 1-2 samples warn for review; >= STOP_SAMPLES distinct
# sample times in an event stop. Many isolated events flag review, never stop.
EVENT_GAP = 0.45        # s; unexplained samples closer than this form one event (5 fps = 0.2 s)
STOP_SAMPLES = 3        # unexplained face in >= 3 sample times of one event -> stop
EVENTS_PER_HOUR = 6     # more isolated events than this per hour -> "review" flag only (no stop)
PIXELATED_COVER = 0.5   # >= 50% of the detection inside a pixelated region = already blurred


class _Decisions:
    def __init__(self, data: dict):
        from reel.blur import _LecturerBoxes
        self.fps = float(data["fps"])
        self.lecturer = _LecturerBoxes([{"t": r[0], "box": r[1:5], "lecturer": True}
                                        for r in data["lecturer"]])
        self.unsure = _LecturerBoxes([{"t": r[0], "box": r[1:5], "lecturer": False, "state": "unsure"}
                                     for r in data.get("unsure", [])], "unsure")
        self.conservative = (data.get("policy") == "confident_students"
                             or ("policy" not in data and bool(data["lecturer"])))
        self.pixelated: dict[int, list] = {}
        for f, left, top, right, bottom in data["pixelated"]:
            self.pixelated.setdefault(int(f), []).append((left, top, right, bottom))

    def explain(self, box, t: float) -> str | None:
        if self.lecturer.matches(tuple(box), t):
            return "blur_track"
        if self.unsure.matches(tuple(box), t):
            return "unsure"
        x, y, w, h = box
        area = w * h
        frame = round(t * self.fps)
        for f in (frame - 1, frame, frame + 1):
            for left, top, right, bottom in self.pixelated.get(f, ()):
                ix = max(0, min(x + w, right) - max(x, left))
                iy = max(0, min(y + h, bottom) - max(y, top))
                if area > 0 and ix * iy / area >= PIXELATED_COVER:
                    return "pixelated"
        return None


def judge(detections: list[dict], decisions: dict | None, duration: float) -> dict:
    """Unsure faces count only; sustained unblurred students stop.

    Missing identity evidence is unsure. Explicit no-lecturer fallback sidecars
    retain the previous blur-everyone enforcement.
    """
    dec = _Decisions(decisions) if decisions else None
    lecturer = by_track = pixelated = unsure = 0
    unexplained = []
    for d in detections:
        if d["lecturer"]:
            lecturer += 1
            continue
        why = dec.explain(d["box"], d["t"]) if dec else None
        if why == "blur_track":
            by_track += 1
        elif why == "pixelated":
            pixelated += 1
        elif why == "unsure" or (d.get("state", "unsure") != "student"
                                  and (dec is None or dec.conservative)):
            unsure += 1
        else:
            unexplained.append(d)
    events: list[list[dict]] = []
    for d in sorted(unexplained, key=lambda d: d["t"]):
        if events and d["t"] - events[-1][-1]["t"] <= EVENT_GAP:
            events[-1].append(d)
        else:
            events.append([d])
    sustained = [e for e in events if len({d["t"] for d in e}) >= STOP_SAMPLES]
    allowed = max(3, math.ceil(EVENTS_PER_HOUR * duration / 3600))
    reason = None
    if sustained:
        ts = ", ".join(f"{e[0]['t']:.1f}s" for e in sustained[:8])
        reason = f"{len(sustained)} student face event(s) visible after blur (at {ts})"
    stop_ids = {id(d) for e in sustained for d in e}      # isolated events never stop (see above)
    return {"lecturer": lecturer, "by_track": by_track, "pixelated": pixelated, "unsure": unsure,
            "events": [{"start": e[0]["t"], "end": e[-1]["t"], "samples": len({d["t"] for d in e}),
                        "stop": any(id(d) in stop_ids for d in e)} for e in events],
            "hits": [d for d in unexplained if id(d) in stop_ids],
            "warnings": [d for d in unexplained if id(d) not in stop_ids], "reason": reason,
            "isolated": len(events) - len(sustained), "review": len(events) - len(sustained) > allowed}


def _save_crops(video: str | Path, items: list[dict], out_dir: Path) -> None:
    capture = cv2.VideoCapture(str(video))
    try:
        for i, d in enumerate(items[:30]):
            capture.set(cv2.CAP_PROP_POS_MSEC, d["t"] * 1000)
            ok, img = capture.read()
            if not ok:
                continue
            x, y, w, h = d["box"]
            region = img[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)]
            if region.size:
                cv2.imwrite(str(out_dir / f"face_{d['t']:07.1f}s_{i:03d}.jpg"), region)
    finally:
        capture.release()


def _brief(d: dict) -> dict:
    sim = d["sim"]
    return {"t": d["t"], "similarity": round(sim, 3) if sim is not None else None,
            "box": d["box"], "size_px": int(min(d["box"][2:]))}


def scan(video: str | Path, refs_dirs: list[str | Path], out_dir: Path, fps: float = 1.0,
         decisions: str | Path | None = None) -> dict:
    """Guard the BLURRED video; `decisions` = blur_decisions.json written by blur_faces."""
    # Local import avoids a cycle: blur reuses FaceID from this module.
    from reel.blur import analyze, _probe

    analysis_fps = max(5.0, fps)
    detections = analyze(str(video), [str(d) for d in refs_dirs], fps=analysis_fps)
    stream = next(s for s in _probe(Path(video))["streams"] if s.get("codec_type") == "video")
    duration = float(stream.get("duration", 0)) or max((d["t"] for d in detections), default=0.0)
    j = judge(detections, json.loads(Path(decisions).read_text()) if decisions else None, duration)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("face_*.jpg"):          # crops from an earlier attempt
        old.unlink()
    _save_crops(video, j["hits"] + j["warnings"], out_dir)
    report = {"frames": round(duration * analysis_fps), "analysis_fps": analysis_fps,
              "lecturer_frames": j["lecturer"], "lecturer_by_blur_track": j["by_track"],
              "already_blurred": j["pixelated"], "unsure_faces": j["unsure"], "other_faces": len(j["hits"]),
              "warnings": len(j["warnings"]), "isolated_events": j["isolated"], "review": j["review"],
              "events": j["events"][:200],
              "hits": [_brief(d) for d in j["hits"][:200]],
              "warning_faces": [_brief(d) for d in j["warnings"][:200]], "stopped": j["reason"]}
    (out_dir / "privacy.json").write_text(json.dumps(report, indent=1))
    if j["reason"]:
        raise StopJob(f"{j['reason']} — see {out_dir}/privacy.json and face_*.jpg")
    return {k: report[k] for k in ("frames", "lecturer_frames", "lecturer_by_blur_track",
                                   "already_blurred", "unsure_faces", "other_faces", "warnings", "isolated_events")}
