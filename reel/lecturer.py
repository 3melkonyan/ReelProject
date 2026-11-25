"""Automatic lecturer recognition — who is the lecturer of this lesson?

The academy's rule: the person most often in front of the camera, at the board,
is the lecturer. The rendered lesson is sampled every SAMPLE_EVERY seconds, faces
are grouped by identity (SFace, as tools/make_refs.py does) and fragments of one
person (front / three-quarter views) are merged — two faces seen in the same
frame are never merged. The identity present in the most sampled frames is the
lecturer only when it clearly dominates. Its best crops are written as a refs
directory in the tools/make_refs.py format, so the blur and the privacy guard
use them exactly like hand-made reference photos.

Privacy first: a wrong choice would leave a student's face unblurred, so every
doubtful case selects mode "none" and blurs everyone instead of guessing. The
admin can later pick the lecturer on the job page and retry.
A lesson with (almost) no faces needs no lecturer: mode "none" blurs every face.

  python -m reel.lecturer VIDEO [--out DIR] [--compare refs/lecturer1]
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from reel.pipeline import StopJob
from reel.stages.privacy import COSINE_MATCH, FaceID

WIDTH = 1920

SAMPLE_EVERY = 2.0  # s between sampled frames (a 2 h lesson = 3600 frames)
MIN_FACE_PX = 24  # identity only; blur detection also retains 16px faces
MIN_FACE_FRAMES = 15  # fewer sampled frames with any face: nobody to recognise -> blur everyone
DOMINANCE = 2.0  # the lecturer is seen in >= 2x the frames of anyone else ...
MIN_SHARE = 0.4  # ... and in >= 40% of the frames that show any face
CROWD = 0.25  # 2+ other people each seen in >= 25% of the lecturer's frames = a crowd
MERGE_MIN = 3  # only identities seen >= 3 times are merged (noise stays apart)
REFS_KEEP = 8  # reference crops written for the chosen identity
REF_MIN_PX = 28  # reference crops must be reasonably large (clip50: median face 26 px) ...
CORE_SIM = 0.5  # ... and typical of the identity (cosine to its centre)
MIN_REFS = 3
CHOICE_SIM = 0.6  # an admin-chosen identity must be found again this closely
PREVIEW_ROWS, PREVIEW_TILES, TILE = 6, 4, 120
# Other views of the lecturer (profile, looking down) from pass-1 tracks, see track_refs().
# Measured on clip50: with front-only refs the privacy guard flagged 29 lecturer profile/back
# views (27 events > 5 allowed) and stopped; hand-made refs/lecturer1 has profile crops and passed.
STRONG_SIM = 0.5  # a track is surely the lecturer when it matched the refs this well ...
STRONG_HITS = 2  # ... at least this many times
TRACK_MIN_PX = 56  # full-resolution face size (~28 px at the 960 px analysis width)
VIEW_MIN = 3  # a view must be seen at least this many times (one-off crops are not trusted)
EXTRA_KEEP = 8  # extra refs added at most
OTHER_SHARE = 0.1  # people seen in >= 10% of the lecturer's frames must never resemble an extra ref


def _unit(v: np.ndarray) -> np.ndarray | None:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 and math.isfinite(n) else None


class _Samples:
    """Every usable face of the sampled frames: time, frame, box, identity feature, crop."""

    def __init__(self):
        self.t, self.frame, self.box, self.score = [], [], [], []
        self.raw, self.unit, self.jpg = [], [], []
        self.frames = 0
        self.size = (0, 0)

    def finish(self) -> None:
        self.t = np.array(self.t, dtype=np.float64)
        self.frame = np.array(self.frame, dtype=np.int64)
        self.box = np.array(self.box, dtype=np.float64).reshape(-1, 4)
        self.score = np.array(self.score, dtype=np.float64)
        self.unit = np.array(self.unit, dtype=np.float32).reshape(-1, 128)

    def quality(self, i: int) -> float:
        return float(self.box[i, 2] * self.box[i, 3] * self.score[i])


def _keyframes(video: Path, every: float, width: int, *, limit: float | None = None):
    """Frames every `every` s, decoding key frames only: ~7x faster than a full decode
    (measured: 31 s vs 209 s for 50 min). The rendered lesson has a key frame every
    ~0.5 s, so the samples stay evenly spread."""
    probe = (
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        .stdout.strip()
        .split(",")
    )
    w0, h0 = int(probe[0]), int(probe[1])
    w = min(width, w0)
    h = int(round(h0 * w / w0 / 2) * 2)
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-skip_frame",
            "nokey",
            "-i",
            str(video),
            *(["-t", str(limit)] if limit is not None else []),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"fps={1.0 / every},scale={w}:{h}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    size = w * h * 3
    finished = False
    try:
        while (buf := proc.stdout.read(size)) and len(buf) == size:
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        finished = True
    finally:
        if not finished and proc.poll() is None:  # the consumer stopped early
            proc.terminate()
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")[-500:]
        proc.stderr.close()
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg sampling failed ({code}): {err.strip()}")


def _sample(video: Path, every: float, *, limit: float | None = None) -> _Samples:
    fid = FaceID([])
    s = _Samples()
    for img in (
        _keyframes(video, every, WIDTH, limit=limit)
        if limit is not None
        else _keyframes(video, every, WIDTH)
    ):
        h_img, w_img = img.shape[:2]
        s.size = (w_img, h_img)
        for f in fid.faces(img):
            x, y, w, h = (float(v) for v in f[:4])
            if not all(math.isfinite(v) for v in (x, y, w, h)) or min(w, h) < MIN_FACE_PX:
                continue
            try:
                raw = fid.feature(img, f)
            except cv2.error:
                continue
            unit = _unit(raw)
            if unit is None:
                continue
            m = max(w, h) * 0.8  # generous margin, as tools/make_refs.py
            x0, y0 = max(int(x - m), 0), max(int(y - m), 0)
            x1, y1 = min(int(x + w + m), w_img), min(int(y + h + m), h_img)
            ok, jpg = cv2.imencode(".jpg", img[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                continue
            s.t.append(s.frames * every)
            s.frame.append(s.frames)
            s.box.append((x, y, w, h))
            s.score.append(float(f[14]))
            s.raw.append(np.array(raw, dtype=np.float32).reshape(1, 128))
            s.unit.append(unit)
            s.jpg.append(jpg.tobytes())
        s.frames += 1
    s.finish()
    return s


def _cluster(unit: np.ndarray, frame: np.ndarray, sim: float = COSINE_MATCH) -> list[list[int]]:
    """Chronological greedy grouping (as tools/make_refs.py); one face per person per frame."""
    sums: list[np.ndarray] = []
    cents = np.empty((0, unit.shape[1] if unit.ndim == 2 else 128), dtype=np.float32)
    members: list[list[int]] = []
    last: list[int] = []
    for i, f in enumerate(unit):
        best = -1
        if len(members):
            sims = cents @ f
            for k in np.argsort(-sims):
                if sims[k] < sim:
                    break
                if last[k] != frame[i]:
                    best = int(k)
                    break
        if best < 0:
            sums.append(f.astype(np.float64))
            cents = np.vstack([cents, f[None, :]])
            members.append([i])
            last.append(int(frame[i]))
        else:
            sums[best] += f
            cents[best] = (sums[best] / np.linalg.norm(sums[best])).astype(np.float32)
            members[best].append(i)
            last[best] = int(frame[i])
    return members


def _merge(
    members: list[list[int]], unit: np.ndarray, frame: np.ndarray, sim: float = COSINE_MATCH
) -> list[list[int]]:
    """Join fragments of one person: similar centres and never in the same frame together."""
    big = [list(m) for m in members if len(m) >= MERGE_MIN]
    small = [list(m) for m in members if len(m) < MERGE_MIN]
    seen = [set(frame[m].tolist()) for m in big]
    while len(big) > 1:
        cents = np.stack([_unit(unit[m].sum(axis=0)) for m in big])
        sims = cents @ cents.T
        np.fill_diagonal(sims, -1.0)
        pairs = sorted(
            (
                (float(sims[a, b]), a, b)
                for a in range(len(big))
                for b in range(a + 1, len(big))
                if sims[a, b] >= sim
            ),
            reverse=True,
        )
        pair = next(((a, b) for _, a, b in pairs if not (seen[a] & seen[b])), None)
        if pair is None:
            break
        a, b = pair
        big[a] = sorted(big[a] + big[b])
        seen[a] |= seen[b]
        del big[b], seen[b]
    return big + small


def _identities(s: _Samples) -> list[dict]:
    groups = _merge(_cluster(s.unit, s.frame), s.unit, s.frame) if len(s.t) else []
    w_img, h_img = s.size
    out = []
    for idx in groups:
        idx = sorted(idx)
        b = s.box[idx]
        centre = _unit(s.unit[idx].sum(axis=0))
        out.append(
            {
                "idx": idx,
                "centre": centre,
                "presence": len(set(s.frame[idx].tolist())),
                "face_px": int(np.median(np.minimum(b[:, 2], b[:, 3]))),
                "y_centre": round(float(np.median((b[:, 1] + b[:, 3] / 2) / max(h_img, 1))), 2),
                "x_spread": round(float(np.std((b[:, 0] + b[:, 2] / 2) / max(w_img, 1))), 3),
                "first_t": round(float(s.t[idx[0]]), 1),
                "last_t": round(float(s.t[idx[-1]]), 1),
            }
        )
    out.sort(key=lambda d: (-d["presence"], d["first_t"]))
    return out


def _pick_refs(s: _Samples, ident: dict, duration: float) -> list[int]:
    """Large, typical, mutually consistent crops spread over the lesson."""
    spacing = min(300.0, max(30.0, duration / (2 * REFS_KEEP)))
    core = [
        i
        for i in ident["idx"]
        if min(s.box[i, 2], s.box[i, 3]) >= REF_MIN_PX
        and float(s.unit[i] @ ident["centre"]) >= CORE_SIM
    ]
    core.sort(key=lambda i: (-s.quality(i), s.t[i]))
    chosen: list[int] = []
    for i in core:
        if all(abs(s.t[i] - s.t[j]) >= spacing for j in chosen):
            chosen.append(i)
        if len(chosen) >= REFS_KEEP:
            break
    if len(chosen) > 1:  # an isolated crop (matches no other reference) is dropped. Not a
        sims = s.unit[chosen] @ s.unit[chosen].T  # majority rule: front and profile views of
        agree = (sims >= COSINE_MATCH).sum(axis=1) - 1  # one person legitimately match less
        chosen = [i for i, a in zip(chosen, agree, strict=False) if a >= 1]
    return chosen


def _preview(s: _Samples, identities: list[dict], folder: Path) -> None:
    """identity_<rank>.jpg: the identity's best crops in one row (admin-only: may show students)."""
    for k, ident in enumerate(identities[:PREVIEW_ROWS], 1):
        best = sorted(ident["idx"], key=lambda i: -s.quality(i))[:PREVIEW_TILES]
        tiles = []
        for i in best:
            img = cv2.imdecode(np.frombuffer(s.jpg[i], np.uint8), cv2.IMREAD_COLOR)
            tiles.append(
                cv2.resize(img, (TILE, TILE))
                if img is not None
                else np.zeros((TILE, TILE, 3), np.uint8)
            )
        tiles += [np.zeros((TILE, TILE, 3), np.uint8)] * (PREVIEW_TILES - len(tiles))
        cv2.imwrite(
            str(folder / f"identity_{k}.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 90]
        )


def _public(ident: dict, face_frames: int) -> dict:
    return {
        "presence": ident["presence"],
        "share": round(ident["presence"] / max(face_frames, 1), 3),
        **{k: ident[k] for k in ("face_px", "y_centre", "x_spread", "first_t", "last_t")},
    }


def _decide(identities: list[dict], face_frames: int) -> str | None:
    """None = the top identity is clearly the lecturer; else why it is not."""
    top = identities[0]
    others = identities[1:]
    second = max((o["presence"] for o in others), default=0)
    if top["presence"] < MIN_FACE_FRAMES:
        return f"the most frequent face is seen in only {top['presence']} sampled frames"
    if top["presence"] < MIN_SHARE * face_frames:
        return (
            f"the most frequent face is in only {top['presence']} of {face_frames} frames with faces "
            f"(< {MIN_SHARE:.0%})"
        )
    if top["presence"] < DOMINANCE * second:
        return (
            f"two people are seen almost equally often ({top['presence']} and {second} sampled frames; "
            f"the lecturer needs {DOMINANCE:g}x)"
        )
    crowd = [o for o in others if o["presence"] >= CROWD * top["presence"]]
    if len(crowd) >= 2:
        return f"{len(crowd) + 1} people are often in the frame (students in view?)"
    return None


def board_zone(framing, t):
    """The physical board in rendered coordinates, widened for a standing lecturer."""
    if not framing or framing.get("version") != 2:
        return None
    from reel.framing import _project, matrix_at

    row = next((r for r in framing["timeline"] if r["start"] <= t < r["end"]), None)
    if not row or row["mode"] not in ("board", "both") or row.get("corners") is None:
        return None
    q = _project(np.asarray(row["corners"]) * framing["size"], matrix_at(framing, t))
    lo = q.min(0)
    hi = q.max(0)
    delta = hi - lo
    return np.array(
        [lo[0] - 0.1 * delta[0], lo[1], hi[0] + 0.1 * delta[0], hi[1] + 0.25 * delta[1]]
    )


def _position_order(identities, s, framing, keep=None, time_map=None):
    if not framing:
        return identities
    from reel.framing import source_time

    for ident in identities:
        inside = observed = 0
        for i in ident["idx"]:
            t = float(s.t[i])
            if time_map:
                from reel.stages.processing import to_source

                t = to_source(t, time_map)
            elif keep:
                t = source_time(t, keep)
            zone = board_zone(framing, t)
            if zone is None:
                continue
            observed += 1
            x, y, w, h = s.box[i]
            centre = (
                np.array([x + w / 2, y + h / 2])
                * np.array(framing["output_size"])
                / np.array(s.size)
            )
            inside += int((centre >= zone[:2]).all() and (centre <= zone[2:]).all())
        ident["board_zone_share"] = inside / observed if observed else None
    # This only proposes a candidate. _decide still compares ALL other identities.
    return sorted(
        identities,
        key=lambda d: (
            d.get("board_zone_share") is not None and d["board_zone_share"] >= 0.6,
            d["presence"],
        ),
        reverse=True,
    )


def discover(
    video: str | Path,
    out_dir: str | Path,
    *,
    duration: float | None = None,
    choice: dict | None = None,
    every: float = SAMPLE_EVERY,
    framing: dict | None = None,
    keep=None,
    time_map=None,
) -> dict:
    """Find this lesson's lecturer and write their reference crops to out_dir.

    choice (admin, from the job page): {"none": true} = blur everyone, or
    {"centre": [128 floats]} = the identity with this centre is the lecturer.
    Returns {"mode": "auto"|"chosen"|"none", "refs_dir": str|None, ...};
    uncertainty selects "none" (everyone blurred), never a guessed identity.
    """
    started = time.perf_counter()
    video, out_dir = Path(video), Path(out_dir)
    try:
        s = _sample(video, every)
    except Exception as exc:  # noqa: BLE001 - decoding problems are a stage failure, not a decision
        raise RuntimeError(
            f"Automatic lecturer recognition could not read the video: {exc}"
        ) from exc
    if duration is None:
        duration = s.frames * every
    identities = _position_order(_identities(s), s, framing, keep, time_map)
    face_frames = len(set(s.frame.tolist())) if len(s.t) else 0
    summary = {
        "sampled": s.frames,
        "every_sec": every,
        "face_frames": face_frames,
        "identities": [_public(d, face_frames) for d in identities[:PREVIEW_ROWS]],
        "elapsed_sec": None,
        "detection_width": WIDTH,
        "board_zone_shares": [d.get("board_zone_share") for d in identities[:PREVIEW_ROWS]],
    }

    part = out_dir.with_name(out_dir.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    _preview(s, identities, part)
    centres = [d["centre"].round(5).tolist() for d in identities[:PREVIEW_ROWS]]

    def publish(result: dict) -> dict:
        summary["elapsed_sec"] = round(time.perf_counter() - started, 1)
        result = {**result, **summary}
        if result.get("refs_dir"):
            result["refs_dir"] = str(out_dir)  # where the refs live once published below
        (part / "auto.json").write_text(json.dumps({**result, "centres": centres}, indent=1))
        shutil.rmtree(out_dir, ignore_errors=True)
        part.replace(out_dir)
        return result

    if choice and choice.get("none"):
        return publish({"mode": "none", "refs_dir": None, "reason": "admin: blur everyone"})
    if choice and choice.get("centre") is not None:
        want = _unit(np.array(choice["centre"], dtype=np.float32))
        sims = [float(d["centre"] @ want) for d in identities] if want is not None else []
        k = int(np.argmax(sims)) if sims else -1
        if k < 0 or sims[k] < CHOICE_SIM:  # never stop -> blur everyone
            return publish(
                {
                    "mode": "none",
                    "refs_dir": None,
                    "reason": "chosen lecturer not found again",
                    "auto_note": "the lecturer chosen on the job page was not found again — everyone blurred",
                }
            )
        lecturer, mode = identities[k], "chosen"
    elif face_frames < MIN_FACE_FRAMES:
        return publish(
            {
                "mode": "none",
                "refs_dir": None,
                "reason": f"faces in only {face_frames} of {s.frames} sampled frames",
            }
        )
    else:
        why = _decide(identities, face_frames)
        if why:  # unsure -> blur everyone (privacy-safe), never stop
            return publish(
                {
                    "mode": "none",
                    "refs_dir": None,
                    "reason": why,
                    "auto_note": f"lecturer unclear ({why}) — everyone blurred",
                }
            )
        lecturer, mode = identities[0], "auto"

    refs = _pick_refs(s, lecturer, duration)
    if len(refs) < MIN_REFS:
        return publish(
            {
                "mode": "none",
                "refs_dir": None,
                "reason": f"only {len(refs)} clear photos of the lecturer",
                "auto_note": f"only {len(refs)} clear photos of the lecturer (needs {MIN_REFS}) — everyone blurred",
            }
        )
    for k, i in enumerate(refs):
        _write_ref(part, k, s.jpg[i], s.raw[i])
    rank = identities.index(lecturer) + 1
    top = identities[0]["presence"]
    others = [
        d["centre"].tolist()
        for d in identities
        if d is not lecturer and d["presence"] >= max(MIN_FACE_FRAMES, OTHER_SHARE * top)
    ]
    result = publish(
        {
            "mode": mode,
            "refs_dir": str(part),
            "refs": len(refs),
            "lecturer_rank": rank,
            "ref_times": [round(float(s.t[i]), 1) for i in refs],
        }
    )
    return {**result, "others": others}  # for track_refs(); not stored in auto.json


def _write_ref(folder: Path, k: int, jpg: bytes, raw: np.ndarray) -> None:
    """ref_<k>.jpg + .npz with the full-frame alignment's descriptor, as tools/make_refs.py stores it."""
    image = folder / f"ref_{k}.jpg"
    image.write_bytes(jpg)
    np.savez(
        image.with_suffix(".npz"),
        feature=np.asarray(raw, dtype=np.float32).reshape(1, 128),
        image_sha256=np.array(hashlib.sha256(jpg).hexdigest()),
    )


def _frame_at(video: Path, t: float) -> np.ndarray | None:
    """One full-resolution frame at t seconds (accurate seek), or None."""
    try:
        png = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                f"{max(0.0, t):.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) if png else None


def track_refs(video: str | Path, analysis: list[dict], out_dir: str | Path, *, others=()) -> int:
    """Add the lecturer's other views (profile, looking down) to the automatic refs.

    Front refs label whole pass-1 tracks (analyze(keep_features=True)); faces inside a track
    that matched them strongly, but are not recognised themselves, are the lecturer turned
    away. A view seen >= VIEW_MIN times that resembles none of the other frequent people
    (`others`: their identity centres) becomes an extra ref, so the blur keeps the lecturer
    and the privacy guard recognises those views. Returns the number of refs added.
    """
    out_dir = Path(out_dir)
    strong: dict[int, int] = {}
    for a in analysis:
        if a.get("sim") is not None and a["sim"] >= STRONG_SIM:
            strong[a["track"]] = strong.get(a["track"], 0) + 1
    cands, units = [], []
    for a in analysis:
        if (
            strong.get(a.get("track"), 0) >= STRONG_HITS
            and a.get("feat") is not None
            and (a.get("sim") is None or a["sim"] < COSINE_MATCH)
            and min(a["box"][2:]) >= TRACK_MIN_PX
        ):
            u = _unit(a["feat"])
            if u is not None:
                cands.append(a)
                units.append(u)
    if not cands:
        return 0
    unit = np.stack(units)
    if len(others):
        far = (unit @ np.stack([_unit(np.array(o)) for o in others]).T).max(axis=1) < COSINE_MATCH
        cands = [a for a, ok in zip(cands, far, strict=False) if ok]
        unit = unit[far]
    if not cands:
        return 0
    stamp = np.array([round(a["t"] * 1000) for a in cands])  # one face per person per moment
    views = sorted((g for g in _cluster(unit, stamp) if len(g) >= VIEW_MIN), key=len, reverse=True)
    per = max(1, EXTRA_KEEP // max(1, len(views)))
    picks: list[int] = []
    for g in views:
        centre = _unit(unit[g].sum(axis=0))
        core = sorted(
            (i for i in g if float(unit[i] @ centre) >= CORE_SIM),
            key=lambda i: -(cands[i]["box"][2] * cands[i]["box"][3]),
        )
        chosen: list[int] = []
        for i in core:
            if all(abs(cands[i]["t"] - cands[j]["t"]) >= 30 for j in chosen):
                chosen.append(i)
            if len(chosen) >= per:
                break
        picks.extend(chosen)
        if len(picks) >= EXTRA_KEEP:
            break
    n = (
        max(
            (int(p.stem[4:]) for p in out_dir.glob("ref_*.jpg") if p.stem[4:].isdigit()), default=-1
        )
        + 1
    )
    added = 0
    for i in picks[:EXTRA_KEEP]:
        a = cands[i]
        frame = _frame_at(Path(video), a["t"])
        if frame is None:
            continue
        x, y, w, h = a["box"]
        m = max(w, h) * 0.8
        crop = frame[max(int(y - m), 0) : int(y + h + m), max(int(x - m), 0) : int(x + w + m)]
        ok, jpg = (
            cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if crop.size
            else (False, None)
        )
        if not ok:
            continue
        _write_ref(out_dir, n + added, jpg.tobytes(), a["feat"])
        added += 1
    report = out_dir / "auto.json"
    if added and report.is_file():
        data = json.loads(report.read_text())
        data.update(track_refs=added, refs=int(data.get("refs") or 0) + added)
        report.write_text(json.dumps(data, indent=1))
    return added


def relabel(analysis: list[dict], refs_dir: str | Path) -> list[dict]:
    """Re-recognise pass-1 faces with the extended refs; any match labels its whole track
    (the same rule as blur.analyze), so a lecturer seen only in profile is kept too."""
    fid = FaceID([Path(refs_dir)])
    refs = np.stack([u for u in (_unit(r) for r in fid.refs) if u is not None])
    hit = set()
    for a in analysis:
        u = _unit(a["feat"]) if a.get("feat") is not None else None
        if u is not None and float((refs @ u).max()) >= COSINE_MATCH:
            hit.add(a["track"])
    for a in analysis:
        a["lecturer"] = bool(a["lecturer"] or a.get("track") in hit)
    return analysis


def _main() -> None:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("video")
    ap.add_argument("--out", help="where to write the refs (default: a temporary directory)")
    ap.add_argument("--every", type=float, default=SAMPLE_EVERY)
    ap.add_argument("--compare", help="hand-made refs directory to check the choice against")
    a = ap.parse_args()
    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="reel-lecturer-")) / "auto_refs"
    try:
        r = discover(a.video, out, every=a.every)
    except StopJob as e:
        print(f"STOP: {e}")
        r = json.loads((out / "auto.json").read_text())
    r.pop("centres", None)
    print(json.dumps(r, indent=1, ensure_ascii=False))
    print(f"identities: {out}/identity_<rank>.jpg")
    if a.compare and r.get("refs_dir"):
        known = FaceID([Path(a.compare)])
        auto = FaceID([Path(r["refs_dir"])])
        sims = [round(float(known.best_match(f)), 3) for f in auto.refs]
        print(
            f"auto refs vs {a.compare}: best cosine per auto descriptor {sims} (match >= {COSINE_MATCH})"
        )


if __name__ == "__main__":
    _main()


def quick_candidates(video: str | Path, out_dir: str | Path) -> dict:
    """Propose at most three identities from 60 keyframe samples in the first ten minutes."""
    folder = Path(out_dir)
    if folder.is_symlink():
        raise ValueError("Invalid candidate directory")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    videos = list(video) if isinstance(video, (list, tuple)) else [video]
    samples = _Samples()
    elapsed = 0.0
    for item in videos:
        if elapsed >= 600:
            break
        chunk = _sample(Path(item), 10.0, limit=600.0 - elapsed)
        for index in range(len(chunk.t)):
            samples.t.append(float(chunk.t[index]) + elapsed)
            samples.frame.append(int(chunk.frame[index]) + samples.frames)
            samples.box.append(chunk.box[index])
            samples.score.append(chunk.score[index])
            samples.raw.append(chunk.raw[index])
            samples.unit.append(chunk.unit[index])
            samples.jpg.append(chunk.jpg[index])
        samples.frames += chunk.frames
        samples.size = chunk.size
        from reel.stages.transcribe import media_duration

        elapsed += media_duration(item)
    samples.finish()
    identities = _identities(samples)[:3]
    face_frames = len(set(samples.frame.tolist()))
    _preview(samples, identities, folder)
    report = {
        "identities": [_public(person, face_frames) for person in identities],
        "centres": [person["centre"].round(5).tolist() for person in identities],
        "sampled": samples.frames,
        "width": WIDTH,
        "seconds": 600,
    }
    for rank, person in enumerate(identities, 1):
        refs = folder / str(rank)
        refs.mkdir(exist_ok=True)
        for index, sample in enumerate(_pick_refs(samples, person, 600.0)):
            _write_ref(refs, index, samples.jpg[sample], samples.raw[sample])
    (folder / "auto.json").write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
    return report
