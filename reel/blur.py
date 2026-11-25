"""Blur only confidently identified student tracks when a lecturer is found.

Detection/recognition reuse FaceID from the privacy guard. The final
privacy scan must still run: sampled detection and held boxes are not proof
that every face was found. Input video is normalized to a constant frame rate
before piping because rawvideo cannot carry variable frame timestamps.
"""
from __future__ import annotations

from collections import deque
from bisect import bisect_left, bisect_right
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
from typing import BinaryIO

import cv2
import numpy as np

from reel.stages.privacy import FaceID, COSINE_MATCH
from reel.encoding import h264_encoder
from reel.frame_io import FrameIO


def _probe(path: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Cannot probe video {path.name}: {exc}") from exc


def _rotation(stream: dict) -> float:
    rotation = float(stream.get("tags", {}).get("rotate", 0))
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            rotation = float(side["rotation"])
    return rotation


def _sample_aspect(probe: dict) -> Fraction:
    stream = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    try:
        aspect = Fraction(stream.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        aspect = Fraction(1)
    if aspect <= 0:
        aspect = Fraction(1)
    return 1 / aspect if round(_rotation(stream) / 90) % 2 else aspect


def _geometry(probe: dict) -> tuple[int, int, Fraction]:
    streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise RuntimeError("Input has no video stream")
    stream = streams[0]
    width, height = int(stream["width"]), int(stream["height"])
    rotation = _rotation(stream)
    if not math.isfinite(rotation) or not math.isclose(rotation % 90, 0, abs_tol=0.01):
        raise RuntimeError("Normalize non-right-angle video rotation before face blurring")
    # ffmpeg autorotates before writing rawvideo; its buffer size must agree.
    if round(rotation / 90) % 2:
        width, height = height, width
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise RuntimeError("H.264 face blurring requires positive, even video dimensions")
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            rate = Fraction(stream.get(key, "0/1"))
            if 0 < rate <= 1000:
                return width, height, rate
        except (ValueError, ZeroDivisionError):
            continue
    raise RuntimeError("Input has no valid video frame rate")


def _read_frame(stream: BinaryIO, buffer: bytearray) -> bool:
    view = memoryview(buffer)
    received = 0
    while received < len(buffer):
        count = stream.readinto(view[received:])
        if not count:
            if received:
                raise RuntimeError("ffmpeg returned a truncated raw video frame")
            return False
        received += count
    return True


def _iou(a: tuple, b: tuple) -> float:
    x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    intersection = x * y
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0.0


# Quality is measured on the detector input (maximum width 1920), not the
# original 4K frame.
STUDENT_MAX = 0.25
CLEAR_MIN_HEIGHT = 60
CLEAR_MIN_SCORE = 0.90
CLEAR_MAX_YAW = 0.20
MIN_CLEAR_SAMPLES = 3


def _quality(face) -> dict:
    """YuNet: eyes at 4:8, nose at 8:10, confidence at 14.

    yaw is a frontalness proxy, not an angle: nose displacement from the eye
    midpoint projected along the eye axis / eye separation (roll independent).
    Missing/degenerate landmarks never provide negative identity evidence.
    """
    result = {"clear": False, "yaw": None, "score": None}
    if len(face) < 15 or not np.isfinite(face).all():
        return result
    eye_a, eye_b, nose = np.asarray(face[4:6]), np.asarray(face[6:8]), np.asarray(face[8:10])
    axis = eye_b - eye_a
    distance2 = float(axis @ axis)
    if distance2 <= 1:
        return result
    yaw = abs(float((nose - (eye_a + eye_b) / 2) @ axis)) / distance2
    score = float(face[14])
    result.update(yaw=yaw, score=score,
                  clear=bool(yaw <= CLEAR_MAX_YAW and face[3] >= CLEAR_MIN_HEIGHT
                             and score >= CLEAR_MIN_SCORE))
    return result


SMALL_FACE_PX = 24      # below this (detector px) no identity feature is computed


def _small(item: dict) -> bool:
    """Flag set by _identify; legacy records fall back to their box size."""
    small = item.get("small")
    return bool(small) if small is not None else min(item["box"][2:]) < SMALL_FACE_PX


def _classify_analysis(records: list[dict], *, fallback: bool = False) -> None:
    """Resolve a whole track, including its first samples and relabels.

    A failed/nonfinite recognition or ambiguous similarity vetoes student evidence.
    Sub-24px tracks cannot establish identity and are blurred by default.
    Larger poor-geometry samples supply no evidence but do not erase clear ones.
    """
    # A supplied analysis may have stale flags. Raw positive evidence
    # still disables the global no-lecturer fallback before any track is resolved.
    fallback = fallback and not any(
        a.get("lecturer") or (isinstance(a.get("sim"), (int, float))
                              and math.isfinite(a["sim"]) and a["sim"] >= COSINE_MATCH)
        for a in records)
    groups: dict[object, list] = {}
    for i, item in enumerate(records):
        groups.setdefault(item.get("track", ("legacy", i)), []).append(item)
    for items in groups.values():
        # Sub-24px samples carry no identity (no recognition is run on them): they
        # neither veto nor support a verdict, so a track that also has large clear
        # student samples is still blurred instead of becoming visible "unsure".
        evidence = [a for a in items if not _small(a)]
        sims = [a.get("sim") for a in evidence]
        finite = [isinstance(v, (int, float)) and math.isfinite(v) for v in sims]
        known = any(a.get("lecturer") for a in items) or any(
            ok and sim >= COSINE_MATCH for ok, sim in zip(finite, sims))
        clear = [a for a in evidence if a.get("clear")]
        student = (all(finite) and len(clear) >= MIN_CLEAR_SAMPLES
                   and all(a["sim"] < STUDENT_MAX for a in clear)
                   and not any(STUDENT_MAX <= v < COSINE_MATCH for v in sims))
        small_only = not evidence
        state = "lecturer" if known else "student" if fallback or student or small_only else "unsure"
        for item in items:
            item["lecturer"] = known
            item["state"] = state


class _Tracker:
    """One-to-one overlap/centre/size association with a two-second grace gap."""
    def __init__(self, max_gap: float = 2.0):
        self.max_gap = max_gap
        self.tracks: list[dict] = []
        self.current: list[dict] = []

    def update(self, boxes: list[tuple], recognized: list[bool], t: float) -> list[bool]:
        self.tracks = [track for track in self.tracks if t - track["seen"] <= self.max_gap + 1e-9]
        candidates = []
        for i, box in enumerate(boxes):
            for j, track in enumerate(self.tracks):
                previous = track["box"]
                overlap = _iou(box, previous)
                size_ratio = max(box[2] / previous[2], previous[2] / box[2],
                                 box[3] / previous[3], previous[3] / box[3])
                distance = math.hypot(box[0] + box[2] / 2 - previous[0] - previous[2] / 2,
                                      box[1] + box[3] / 2 - previous[1] - previous[3] / 2)
                scale = max(math.hypot(box[2], box[3]), math.hypot(previous[2], previous[3]))
                if overlap > 0.3 or (size_ratio <= 2.0 and distance <= scale * 0.8):
                    candidates.append((overlap + 0.25 * (1 - distance / scale), i, j))
        candidates.sort(reverse=True)
        assigned, used = {}, set()
        for _, i, j in candidates:
            if i not in assigned and j not in used:
                assigned[i] = j
                used.add(j)
        result = []
        self.current = []
        for i, box in enumerate(boxes):
            if i in assigned:
                track = self.tracks[assigned[i]]
                track.update(box=box, seen=t, lecturer=track["lecturer"] or recognized[i])
            else:
                track = {"box": box, "seen": t, "lecturer": recognized[i]}
                self.tracks.append(track)
            self.current.append(track)
            result.append(bool(track["lecturer"]))
        return result


def _identify(fid: FaceID, image: np.ndarray, tracker: _Tracker, t: float, keep: bool = False) -> list[dict]:
    boxes, similarities, recognized, features, qualities = [], [], [], [], []
    for face in fid.faces(image):
        box = tuple(float(value) for value in face[:4])
        if len(box) != 4 or not all(math.isfinite(value) for value in box) or min(box[2:]) <= 0:
            raise RuntimeError("Face detector returned an invalid bounding box")
        similarity, feature = float("nan"), None
        if fid.refs and min(box[2:]) >= SMALL_FACE_PX:
            try:
                feature = fid.feature(image, face)
                similarity = float(fid.best_match(feature))
            except Exception:
                feature = None  # Recognition failure supplies no student evidence.
        qualities.append(_quality(face))
        boxes.append(box)
        similarities.append(similarity if math.isfinite(similarity) else None)
        recognized.append(math.isfinite(similarity) and similarity >= COSINE_MATCH)
        features.append(feature)
    identities = tracker.update(boxes, recognized, t)
    items = [{"box": list(box), "lecturer": known, "sim": sim, "small": min(box[2:]) < SMALL_FACE_PX, **quality}
             for box, known, sim, quality in zip(boxes, identities, similarities, qualities)]
    if keep:     # identity features for reel.lecturer.track_refs
        for item, feature in zip(items, features):
            item["feat"] = feature
    return items


class _LecturerBoxes:
    """Time-indexed lecturer xywh boxes from the complete first analysis pass."""
    def __init__(self, records: list[dict], state: str = "lecturer"):
        ordered = sorted((float(item["t"]), tuple(item["box"]))
                         for item in records if ("lecturer" if item["lecturer"] else item.get("state", "unsure")) == state)
        self.times = [t for t, _ in ordered]
        self.boxes = [box for _, box in ordered]

    def matches(self, box: tuple, t: float) -> bool:
        first = bisect_left(self.times, t - 0.3 - 1e-9)
        last = bisect_right(self.times, t + 0.3 + 1e-9)
        return any(_iou(box, self.boxes[i]) > 0.3 for i in range(first, last))


class _BlurPolicy(_LecturerBoxes):
    def __init__(self, records: list[dict], log_unsure: bool = True):
        self.log_unsure = log_unsure
        self.protected: list[tuple] = []
        self.fallback = not any(a["lecturer"] for a in records)
        super().__init__(records)
        self.students = _LecturerBoxes(records, "student")
        self.unsure = _LecturerBoxes(records, "unsure")
        self.observed_unsure: list[dict] = []
        self._logged: set[tuple] = set()      # one entry per face per 0.2 s, not per output frame

    def state(self, box: tuple, t: float) -> str:
        if self.matches(box, t):
            return "lecturer"
        if self.fallback or min(box[2:]) < SMALL_FACE_PX:
            return "student"
        # Ambiguous association always favours leaving a face visible.
        if self.unsure.matches(box, t) or not self.students.matches(box, t):
            # The guard samples at 1 Hz and matches within +-0.3 s, so 5 Hz is enough: a 2-hour
            # lesson with one visible unsure face logs ~36k entries instead of ~216k (30 fps).
            key = (round(t * 5), round(box[0] / 16), round(box[1] / 16))
            if self.log_unsure and key not in self._logged:
                self._logged.add(key)
                self.observed_unsure.append({"t": t, "box": list(box),
                                             "lecturer": False, "state": "unsure"})
            return "unsure"
        return "student"


def _detections(fid: FaceID, frame: np.ndarray, tracker: _Tracker | None = None,
                t: float = 0.0, lecturer_boxes: _LecturerBoxes | None = None
                ) -> tuple[list[tuple[int, int, int, int]], int, int]:
    height, width = frame.shape[:2]
    small_width = min(1920, width)
    small_height = max(1, round(height * small_width / width))
    image = cv2.resize(frame, (small_width, small_height)) if small_width < width else frame
    sx, sy = width / small_width, height / small_height
    boxes, lecturer, uncertain = [], 0, 0
    if isinstance(lecturer_boxes, _BlurPolicy):
        lecturer_boxes.protected = []
    if lecturer_boxes is None:
        items = _identify(fid, image, tracker if tracker is not None else _Tracker(), t)
    else:
        items = []
        for face in fid.faces(image):
            box = tuple(float(value) for value in face[:4])
            if len(box) != 4 or not all(math.isfinite(value) for value in box) or min(box[2:]) <= 0:
                raise RuntimeError("Face detector returned an invalid bounding box")
            x, y, w, h = box
            full_box = (x * sx, y * sy, w * sx, h * sy)
            state = (lecturer_boxes.state(full_box, t) if isinstance(lecturer_boxes, _BlurPolicy)
                     else "lecturer" if lecturer_boxes.matches(full_box, t) else "unsure")
            if state != "student" and isinstance(lecturer_boxes, _BlurPolicy):
                lecturer_boxes.protected.append(full_box)
            items.append({"box": list(box), "lecturer": state == "lecturer", "state": state})
    for item in items:
        if item["lecturer"]:
            lecturer += 1
            continue
        uncertain += 1
        if item.get("state", "student" if not fid.refs else "unsure") != "student":
            continue
        x, y, w, h = item["box"]
        left = max(0, math.floor((x - w * 0.15) * sx))
        top = max(0, math.floor((y - h * 0.15) * sy))
        right = min(width, math.ceil((x + w * 1.15) * sx))
        bottom = min(height, math.ceil((y + h * 1.15) * sy))
        if left >= right or top >= bottom:
            raise RuntimeError("Detected face lies outside the video frame")
        boxes.append((left, top, right, bottom))
    return boxes, lecturer, uncertain


def analyze(video: str, refs_dirs: list[str], fps: float = 5.0, keep_features: bool = False) -> list[dict]:
    """Sample visible faces without encoding; boxes are full-resolution xywh.

    keep_features adds "track" (an id shared by one tracked face) and
    "feat" (its SFace feature, or None) to every item.

    Any reference match labels the entire track, including earlier samples.
    Gaps over 2s expire tracks; new tracks must independently match a reference.
    No refs_dirs at all ("none": nobody is the lecturer) labels every face a
    non-lecturer; refs_dirs given but without a usable face is still an error.
    """
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise RuntimeError("Analysis fps must be finite and positive")
    source = Path(video).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"Analysis input does not exist: {source}")
    process = None
    try:
        observation_probe = _probe(source)
        from reel.progress import media_seconds
        observation_duration = media_seconds(observation_probe)
        width, height, _ = _geometry(observation_probe)
        small_width = min(1920, width)
        small_height = max(2, round(height * small_width / width / 2) * 2)
        sx, sy = width / small_width, height / small_height
        fid = FaceID([Path(d).expanduser().resolve() for d in refs_dirs])
        if refs_dirs and not fid.refs:
            raise RuntimeError("No usable lecturer reference photos; regenerate refs first")
        tracker, result, index = _Tracker(max_gap=max(2.0, 1.0 / fps)), [], 0
        with tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen([
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                    "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                    "-vf", f"fps=fps={fps}:start_time=0,scale={small_width}:{small_height}",
                    "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
                ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr)
                with FrameIO(lambda buffer: _read_frame(process.stdout, buffer), None,
                             small_width * small_height * 3, lambda: _stop(process)) as frames_io:
                    for buffer in frames_io:
                        image = np.frombuffer(buffer, dtype=np.uint8).reshape(small_height, small_width, 3)
                        t = index / fps
                        items = _identify(fid, image, tracker, t, keep_features)
                        for item, track in zip(items, tracker.current):
                            x, y, w, h = item["box"]
                            item["box"] = [round(x * sx), round(y * sy), round(w * sx), round(h * sy)]
                            result.append({"t": round(t, 6), **item, "_track": track})
                        index += 1
                        from reel.progress import report
                        report(index / fps, observation_duration, kind="faces")
                        frames_io.release(buffer)
                process.stdout.close()
                if process.wait(timeout=60):
                    raise RuntimeError(f"ffmpeg face analysis failed: {_log(stderr)}")
                if not index:
                    raise RuntimeError("Face analysis decoded no frames")
            finally:
                _stop(process)
                if process is not None and process.stdout is not None:
                    process.stdout.close()
        ids: dict[int, int] = {}
        for item in result:          # every track object is still alive here: ids are unique
            ids.setdefault(id(item["_track"]), len(ids))
        for item in result:
            track = item.pop("_track")
            item["lecturer"] = bool(track["lecturer"])
            item["track"] = ids[id(track)]
        _classify_analysis(result, fallback=not any(a["lecturer"] for a in result))
        return result
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Face analysis failed ({type(exc).__name__}): {exc}") from exc


def _pixelate(frame: np.ndarray, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    roi = frame[top:bottom, left:right]
    # Tiny faces need full concealment; resizing to the same size is not blur.
    if min(roi.shape[:2]) < 12:
        roi[:] = 0
        return
    # At most 6x6 cells, with each cell at least 12 source pixels wide/tall.
    tiny = cv2.resize(roi, (min(6, max(1, roi.shape[1] // 12)),
                           min(6, max(1, roi.shape[0] // 12))), interpolation=cv2.INTER_AREA)
    roi[:] = cv2.resize(tiny, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_NEAREST)


def _retained(history: deque, frame_index: int, boxes: list, hold_frames: int) -> list:
    while history and history[0][0] < frame_index:
        history.popleft()
    if boxes:
        history.append((frame_index + hold_frames, boxes))
    # Exact duplicates do not need a second pixelation, but previous locations
    # survive independently even when a later detection identifies a lecturer.
    return list(dict.fromkeys(box for _, batch in history for box in batch))


def _stop(process: subprocess.Popen | None) -> None:
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _log(stream: BinaryIO) -> str:
    stream.seek(0)
    return stream.read().decode("utf-8", errors="replace")[-4000:].strip()


def _write_decisions(path: Path, fps: float, analysis: list[dict], pixelated: list) -> None:
    """Blur decisions for the privacy guard: pass-1 lecturer boxes [t,x,y,w,h]
    and unsure boxes, plus every pixelated region actually applied."""
    data = {"fps": fps,
            "lecturer": [[round(float(a["t"]), 3), *a["box"]] for a in analysis if a["lecturer"]],
            "unsure": [[round(float(a["t"]), 3), *a["box"]] for a in analysis
                       if not a["lecturer"] and a.get("state") == "unsure"],
            "policy": "confident_students" if any(a["lecturer"] for a in analysis) else "fallback",
            "pixelated": pixelated}
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, separators=(",", ":")))
    part.replace(path)


def blur_faces(video_in: str, video_out: str, refs_dirs: list[str], *,
               detect_every: int = 3, hold_sec: float = 0.7,
               analysis: list[dict] | None = None, decisions_path: str | None = None) -> dict:
    """Write a full-size, atomically published MP4 with unchanged audio packets.

    blurred_boxes counts region applications across all output frames;
    lecturer_boxes/uncertain_boxes count detections on pass-two frames.
    uncertain_boxes includes all non-lecturer detections (including students).
    With a lecturer found, only student tracks are blurred; unassigned pass-two
    detections are unsure. Without a lecturer match every detection is blurred.
    fps_processed includes the complete first analysis pass, probing, model
    loading, second-pass detection/encoding and final muxing (unless analysis
    is supplied). Pass two uses resolved student boxes within +/-0.3 seconds.
    Pass two detects every output frame; detect_every is retained only for
    compatibility with older callers and must remain a positive integer.
    """
    started = time.perf_counter()
    if isinstance(detect_every, bool) or not isinstance(detect_every, int) or detect_every < 1:
        raise RuntimeError("detect_every must be a positive integer")
    if isinstance(hold_sec, bool) or not isinstance(hold_sec, (int, float)) or not math.isfinite(hold_sec) or hold_sec < 0:
        raise RuntimeError("hold_sec must be a finite, non-negative number")
    if not isinstance(refs_dirs, list) or any(not isinstance(d, (str, Path)) for d in refs_dirs):
        raise RuntimeError("refs_dirs must be a list of reference directories")
    source, output = Path(video_in).expanduser().resolve(), Path(video_out).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"Input video does not exist: {source}")
    if source == output or (output.exists() and source.samefile(output)):
        raise RuntimeError("Input and output video must be different files")
    if output.suffix.lower() != ".mp4":
        raise RuntimeError("Face blur output must have the .mp4 extension")
    decoder = encoder = None
    temporary = None
    try:
        # Offline first pass resolves complete tracks, including future matches.
        if analysis is None:   # callers may pass a precomputed analyze(fps=5) result
            analysis = analyze(str(source), refs_dirs, fps=5.0) if refs_dirs else []
        # Lecturer selection may have changed flags after analyze(). Re-resolve
        # here without changing lecturer selection or its feature descriptors.
        _classify_analysis(analysis, fallback=not any(a["lecturer"] for a in analysis))
        lecturer_boxes = _BlurPolicy(analysis, log_unsure=bool(decisions_path))
        source_probe = _probe(source)
        width, height, rate = _geometry(source_probe)
        aspect = _sample_aspect(source_probe)
        fid = FaceID([Path(directory).expanduser().resolve() for directory in refs_dirs])
        output.parent.mkdir(parents=True, exist_ok=True)
        # Unique sibling keeps concurrent/crashed attempts away from final output.
        with tempfile.NamedTemporaryFile(prefix=f".{output.stem}-", suffix=".part.mp4", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        rate_arg = f"{rate.numerator}/{rate.denominator}"
        # Pass two detects every frame; hold only supplies temporal coverage.
        hold_frames = math.ceil(hold_sec * float(rate))
        history: deque = deque()
        frames = blurred = lecturers = uncertain = 0
        pix_log: list | None = [] if decisions_path else None
        from reel.progress import EncoderChannel, media_seconds
        with EncoderChannel(media_seconds(source_probe)) as observation, tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
            try:
                decoder = subprocess.Popen([
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                    "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                    "-vf", f"fps=fps={rate_arg}:start_time=0", "-pix_fmt", "bgr24",
                    "-f", "rawvideo", "pipe:1",
                ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=decode_log)
                encoder = subprocess.Popen([
                    "ffmpeg", *observation.args, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pix_fmt", "bgr24", "-video_size", f"{width}x{height}",
                    "-framerate", rate_arg, "-i", "pipe:0", "-i", str(source),
                    "-map", "0:v:0", "-map", "1:a?", "-map_metadata", "-1",
                    # RGB -> YUV with BT.709 and bt709 tags (ffmpeg's default is BT.601, untagged)
                    "-vf", f"setsar={aspect.numerator}/{aspect.denominator},setparams=color_primaries=bt709"
                           ":color_trc=bt709,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-c:v", h264_encoder(), "-b:v", "6M", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(temporary),
                ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log)
                def abort_io():
                    _stop(decoder)
                    _stop(encoder)

                with FrameIO(lambda buffer: _read_frame(decoder.stdout, buffer),
                             encoder.stdin.write, width * height * 3, abort_io) as frames_io:
                    for buffer in frames_io:
                        frame = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3)
                        boxes, known, unknown = _detections(fid, frame, None, frames / float(rate), lecturer_boxes)
                        lecturers += known
                        uncertain += unknown
                        for box in _retained(history, frames, boxes, hold_frames):
                            # A held/margined student region must not cover a face
                            # explicitly kept on this frame after it moved into it.
                            left, top, right, bottom = box
                            if any(min(right, x + w) > max(left, x)
                                   and min(bottom, y + h) > max(top, y)
                                   for x, y, w, h in lecturer_boxes.protected):
                                continue
                            _pixelate(frame, box)
                            blurred += 1
                            if pix_log is not None:   # logged where applied, so the log = pixels
                                pix_log.append([frames, *box])
                        frames_io.release(buffer)
                        frames += 1
                        observation.poll()
                decoder.stdout.close()
                encoder.stdin.close()
                decode_status = decoder.wait(timeout=60)
                encode_status = encoder.wait(timeout=120)
                if decode_status or encode_status:
                    raise RuntimeError(
                        f"ffmpeg failed (decode={decode_status}, encode={encode_status}): "
                        f"{_log(decode_log)} {_log(encode_log)}"
                    )
                if frames == 0:
                    raise RuntimeError("Input video decoded no frames")
            except Exception as exc:
                _stop(decoder)
                _stop(encoder)
                if isinstance(exc, (BrokenPipeError, subprocess.TimeoutExpired)):
                    raise RuntimeError(f"ffmpeg face blur pipe failed: {_log(decode_log)} {_log(encode_log)}") from exc
                raise
            finally:
                _stop(decoder)
                _stop(encoder)
                if decoder is not None and decoder.stdout is not None:
                    decoder.stdout.close()
                if encoder is not None and encoder.stdin is not None:
                    try:
                        encoder.stdin.close()
                    except BrokenPipeError:
                        pass
        if not temporary.stat().st_size:
            raise RuntimeError("ffmpeg produced an empty blurred video")
        if decisions_path:
            _write_decisions(Path(decisions_path).expanduser().resolve(), float(rate),
                             analysis + lecturer_boxes.observed_unsure, pix_log)
        temporary.replace(output)
        return {"frames": frames, "fps_processed": round(frames / (time.perf_counter() - started), 3),
                "blurred_boxes": blurred, "lecturer_boxes": lecturers, "uncertain_boxes": uncertain,
                "student_tracks": len({a.get("track", i) for i, a in enumerate(analysis) if a["state"] == "student"}),
                "unsure_tracks": len({a.get("track", i) for i, a in enumerate(analysis) if a["state"] == "unsure"})}
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Face blurring failed ({type(exc).__name__}): {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _unit_smoke() -> None:
    """Check privacy decisions in memory, independently of detector recall."""
    import io

    class FakeID:
        refs = [True]

        def faces(self, image):
            self.image = image
            return np.array([[0, 0, 30, 30], [70, 40, 30, 30], [120, 40, 30, 30], [160, 40, 30, 30]], dtype=np.float32)

        def feature(self, image, face):
            assert image is self.image
            if face[0] == 160:
                raise RuntimeError("recognition failed")
            return face[0]

        def best_match(self, feature):
            return {0: COSINE_MATCH + 0.1, 70: COSINE_MATCH - 0.1, 120: float("nan")}[feature]

    image = np.random.default_rng(1).integers(0, 256, (200, 400, 3), dtype=np.uint8)
    before = image.copy()
    fake = FakeID()
    boxes, known, unknown = _detections(fake, image)
    assert (known, unknown, len(boxes)) == (1, 3, 0)
    for box in boxes:
        _pixelate(image, box)
    assert np.array_equal(image[:20, :20], before[:20, :20])
    assert np.array_equal(image, before)  # uncertain/NaN/recognition error stay visible
    tiny = np.full((4, 4, 3), 200, dtype=np.uint8)
    _pixelate(tiny, (0, 0, 4, 4))
    assert not tiny.any()
    fake.refs = []
    boxes, known, unknown = _detections(fake, image)
    assert known == 0 and unknown == 4 and boxes[0] == (0, 0, 35, 35)
    tracker = _Tracker()
    assert tracker.update([(0, 0, 20, 20)], [True], 0.0) == [True]
    assert tracker.update([(2, 0, 20, 20), (100, 0, 20, 20)], [False, False], 0.2) == [True, False]
    assert tracker.update([(100, 0, 20, 20)], [False], 1.1) == [False]
    assert tracker.update([(100, 0, 20, 20)], [True], 1.2) == [True]
    assert tracker.update([(100, 0, 20, 20), (102, 0, 20, 20)], [False, False], 1.3).count(True) == 1
    anchors = _LecturerBoxes([{"t": 1.0, "box": [0, 0, 20, 20], "lecturer": True},
                             {"t": 1.0, "box": [100, 0, 20, 20], "lecturer": False}])
    assert anchors.matches((0, 0, 20, 20), 0.7)
    assert anchors.matches((0, 0, 20, 20), 1.3)
    assert not anchors.matches((0, 0, 20, 20), 0.69)
    assert not anchors.matches((100, 0, 20, 20), 1.0)
    history = deque()
    assert _retained(history, 0, boxes[:1], 3) == boxes[:1]
    assert _retained(history, 1, [], 3) == boxes[:1]
    assert _retained(history, 3, [], 3) == boxes[:1]
    assert _retained(history, 4, [], 3) == []
    try:
        _read_frame(io.BytesIO(b"short"), bytearray(10))
        raise AssertionError("Truncated frame was accepted")
    except RuntimeError:
        pass
    print("OK: lecturer exemption, uncertain/NaN/failed recognition kept, missing refs fallback, margin/clipping, hold/expiry, truncated frame")


def _audio_hashes(path: Path) -> list[str]:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_packets",
        "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    return [packet["data_hash"] for packet in json.loads(result.stdout).get("packets", [])]


def _smoke() -> None:
    _unit_smoke()
    root = Path(__file__).resolve().parents[1]
    lesson, refs = root / "samples/lesson21.mp4", root / "refs/lecturer1"
    real = lesson.is_file() and refs.is_dir()
    with tempfile.TemporaryDirectory(prefix="reel-blur-smoke-") as directory:
        source, output = Path(directory) / "source.mp4", Path(directory) / "blurred.mp4"
        if real:
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                       "-i", str(lesson), "-t", "30", "-map", "0:v:0", "-map", "0:a?", "-c", "copy", str(source)]
        else:
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=3",
                       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                       "-c:v", h264_encoder(), "-b:v", "6M", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)]
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
        report = blur_faces(str(source), str(output), [str(refs)] if real else [])
        probe = _probe(output)
        width, height, rate = _geometry(probe)
        assert report["frames"] > 0 and report["fps_processed"] > 0
        assert (width, height) == _geometry(_probe(source))[:2]
        assert _audio_hashes(source) == _audio_hashes(output), "Audio packets changed"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
                       capture_output=True, text=True, check=True, timeout=120)
        assert not list(Path(directory).glob("*.part.mp4"))
        if not real:
            assert report["frames"] == 90 and report["blurred_boxes"] == 0
            assert report["lecturer_boxes"] == report["uncertain_boxes"] == 0
        print(json.dumps({"mode": "lesson_first_30s" if real else "testsrc_no_faces", "width": width,
                          "height": height, "fps": float(rate), **report}, ensure_ascii=False))
        print("OK: full-size H.264 output decodes, audio packet hashes unchanged, atomic output; temporary smoke files removed on exit")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    try:
        _smoke()
    except Exception as error:
        parser.exit(1, f"Face blur smoke failed: {error}\n")
