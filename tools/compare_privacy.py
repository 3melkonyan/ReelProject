"""Compare every reference blur region at the same output frame.

    python tools/compare_privacy.py out/performance/candidate/blur_decisions.json \
        --reference out/performance/reference/blur_decisions.json \
        --reference-metrics out/performance/reference/profile.json \
        --new-metrics out/performance/candidate/profile.json \
        --privacy-output out/performance/candidate/privacy/privacy.json \
        --output out/performance/comparison.json

Every reference rectangle must remain fully covered, regardless of size.
Metrics must include a blur stage; the independent scan report supplies sampling
metadata. Outputs are restricted to this checkout's ignored out/performance/.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]


def _number(value, label: str, *, minimum: float = 0, integer: bool = False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < minimum
            or (integer and int(value) != value)):
        raise ValueError(f"{label} must be a finite {'integer' if integer else 'number'} >= {minimum}")
    return int(value) if integer else float(value)


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def read_json(path: str | Path) -> dict:
    # Reject duplicate keys rather than silently accepting conflicting metadata.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return _object(json.loads(Path(path).read_text(), object_pairs_hook=unique), str(path))


def read_stage_metrics(path: str | Path, job_id: int = 1) -> dict:
    """Read a completed, checkpointed DB without creating WAL/SHM sidecars.

    immutable=1 suppresses SQLite's otherwise possible sidecar writes even with
    mode=ro. It ignores WAL, so refuse a nonempty WAL instead of reading stale
    metrics. For a live DB, export explicit metrics JSON after the job finishes.
    """
    _number(job_id, "job_id", minimum=1, integer=True)
    source = Path(path).expanduser().resolve()
    wal = source.with_name(source.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("nonempty SQLite WAL; use completed checkpointed DB or explicit metrics JSON")
    uri = source.as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT name, status, output FROM stages WHERE job_id=? AND name IN ('blur', 'privacy')",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    result = {}
    for name, status, output in rows:
        if status != "done":
            raise ValueError(f"job {job_id} {name} stage is {status}, not done")
        result[name] = _object(json.loads(output), f"{name} stage")
    if "blur" not in result:
        raise ValueError(f"job {job_id} has no completed blur stage")
    return result


def _validate(data: dict, metrics: dict, label: str) -> tuple[float, int]:
    _object(data, label)
    _object(metrics, f"{label} metrics")
    blur = _object(metrics.get("blur"), f"{label} metrics.blur")
    fps = _number(data.get("fps"), f"{label}.fps", minimum=1e-9)
    frames = _number(blur.get("frames"), f"{label} blur.frames", minimum=1, integer=True)
    duration = frames / fps
    if data.get("policy") not in ("confident_students", "fallback"):
        raise ValueError(f"{label}.policy is missing or unknown")
    for key in ("lecturer", "unsure", "pixelated"):
        rows = _list(data.get(key), f"{label}.{key}")
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 5:
                raise ValueError(f"{label}.{key}[{i}] must have five numbers")
            if key == "pixelated":
                frame = _number(row[0], f"{label}.{key}[{i}].frame", integer=True)
                if frame >= frames:
                    raise ValueError(f"{label}.{key}[{i}] is beyond blur.frames")
            else:
                t = _number(row[0], f"{label}.{key}[{i}].t")
                if t >= duration:
                    raise ValueError(f"{label}.{key}[{i}] is beyond blur duration")
            for n, value in enumerate(row[1:], 1):
                # Detector xywh boxes can straddle an image edge; applied
                # pixelation rectangles are clipped and remain nonnegative.
                minimum = -math.inf if key != "pixelated" and n <= 2 else 0
                _number(value, f"{label}.{key}[{i}][{n}]", minimum=minimum)
            if (key == "pixelated" and (row[3] <= row[1] or row[4] <= row[2])
                    or key != "pixelated" and (row[3] <= 0 or row[4] <= 0)):
                raise ValueError(f"{label}.{key}[{i}] has an empty/inverted box")
    count = _number(blur.get("blurred_boxes"), f"{label} blur.blurred_boxes", integer=True)
    if count != len(data["pixelated"]):
        raise ValueError(f"{label} blur.blurred_boxes disagrees with pixelated log")
    share = _number(blur.get("lecturer_share"), f"{label} blur.lecturer_share")
    if share > 1:
        raise ValueError(f"{label} blur.lecturer_share must be <= 1")
    _number(blur.get("lecturer_boxes"), f"{label} blur.lecturer_boxes", integer=True)
    if not isinstance(blur.get("lecturer_mode"), str):
        raise ValueError(f"{label} blur.lecturer_mode must be a string")
    return fps, frames


def _matches(reference, candidate) -> bool:
    """Require full forward containment; overlap alone can expose face pixels."""
    return (candidate[0] <= reference[0] and candidate[1] <= reference[1]
            and candidate[2] >= reference[2] and candidate[3] >= reference[3])


def _bin(size: float) -> str:
    return "ge24" if size >= 24 else "16_to_lt24" if size >= 16 else "lt16"


def compare(reference: dict, new: dict, reference_metrics: dict, new_metrics: dict,
            privacy: dict | None = None) -> dict:
    """Compare every logged reference region, at the identical output frame.

    Invalid metadata raises ValueError; valid but unacceptable results return
    FAIL. Pixelation coverage never matches nearby timestamps. Explicitly open
    lecturer/unsure identities use the policy's +/-0.3s identity window, so sparse
    analysis records cannot hide newly open faces between sample timestamps.
    """
    ref_fps, ref_frames = _validate(reference, reference_metrics, "reference")
    new_fps, new_frames = _validate(new, new_metrics, "new")
    errors = []
    if not math.isclose(ref_fps, new_fps, rel_tol=0, abs_tol=1e-9):
        errors.append("output fps changed; frame-to-time correspondence is invalid")
    if new_frames != ref_frames:
        errors.append("output frame count changed; full-duration coverage is required")
    if not reference["pixelated"]:
        errors.append("reference pixelated log is empty; recall cannot be established")
    if not any(min(r[3] - r[1], r[4] - r[2]) >= 16 for r in reference["pixelated"]):
        errors.append("reference has no >=16px regions; required recall cannot be established")

    pixelated = defaultdict(list)
    for frame, *box in new["pixelated"]:
        pixelated[frame].append(box)
    identities = sorted((t, [x, y, x + w, y + h], kind)
                        for kind in ("lecturer", "unsure")
                        for t, x, y, w, h in new[kind])
    identity_times = [row[0] for row in identities]
    bins = {name: {"total": 0, "matched": 0, "missed": 0, "recall": None}
            for name in ("ge24", "16_to_lt24", "lt16")}
    misses = []
    open_faces = []
    open_lecturers = []
    for index, (frame, left, top, right, bottom) in enumerate(reference["pixelated"]):
        box = [left, top, right, bottom]
        size = min(right - left, bottom - top)
        bucket = bins[_bin(size)]
        bucket["total"] += 1
        covered = any(_matches(box, candidate) for candidate in pixelated.get(frame, ()))
        bucket["matched" if covered else "missed"] += 1
        if not covered:
            item = {"reference_index": index, "frame": frame, "t": frame / ref_fps,
                    "size_px": size, "width": right - left, "height": bottom - top,
                    "box": box, "bin": _bin(size)}
            misses.append(item)
            # Covered boxes remain protected even when an identity record overlaps;
            # such overlap is not evidence of an open face.
            t = frame / new_fps
            first = bisect_left(identity_times, t - 0.3 - 1e-9)
            last = bisect_right(identity_times, t + 0.3 + 1e-9)
            kinds = {kind for _, candidate, kind in identities[first:last] if _matches(box, candidate)}
            if kinds:
                open_faces.append({**item, "open_states": sorted(kinds)})
                if "lecturer" in kinds:
                    open_lecturers.append(item)
    for bucket in bins.values():
        if bucket["total"]:
            bucket["recall"] = bucket["matched"] / bucket["total"]
    if misses:
        errors.append("every reference region must remain fully covered at every size")
    if open_faces:
        errors.append("reference pixelated regions became explicitly open lecturer/unsure regions")

    lecturer_metrics = {}
    ref_blur, new_blur = reference_metrics["blur"], new_metrics["blur"]
    if new_blur["lecturer_mode"] != "auto":
        errors.append("lecturer_mode must be auto")
    if not new["lecturer"]:
        errors.append("new lecturer identity log is empty")
    for key in ("lecturer_share", "lecturer_boxes"):
        before, after = ref_blur[key], new_blur[key]
        within = abs(after - before) <= abs(before) * 0.05 + 1e-12
        lecturer_metrics[key] = {"reference": before, "new": after, "within_5_percent": within}
        if not within:
            errors.append(f"{key} is outside reference ±5%")

    privacy = _object(privacy if privacy is not None else new_metrics.get("privacy"), "new privacy")
    privacy_result = {}
    for key in ("warnings", "other_faces"):
        value = _number(privacy.get(key), f"privacy.{key}", integer=True)
        privacy_result[key] = value
        if value:
            errors.append(f"privacy.{key} must be zero")
    rate = _number(privacy.get("analysis_fps"), "privacy.analysis_fps", minimum=1e-9)
    samples = _number(privacy.get("frames"), "privacy.frames", minimum=1, integer=True)
    expected = (new_frames / new_fps) * rate
    # Existing scan rounds container_duration*analysis_fps. The rational output
    # duration may differ fractionally; allow its floor/ceil, never a full sample
    # less than the floor (in particular 199/200 samples is not full coverage).
    coverage = math.floor(expected + 1e-9) <= samples <= math.ceil(expected - 1e-9)
    if rate < 1:
        errors.append("privacy sampling rate must be >=1Hz")
    if not coverage:
        errors.append("privacy sample count does not cover the full output duration")
    if privacy.get("stopped"):
        errors.append("privacy scan stopped")
    # If a full report overrides a DB stage summary, both must agree on the
    # acceptance counters. A clean stale report must not hide a failed stage.
    summary = new_metrics.get("privacy")
    if summary is not None:
        _object(summary, "new metrics.privacy")
        for key in ("warnings", "other_faces", "frames"):
            value = _number(summary.get(key), f"new metrics.privacy.{key}", integer=True)
            if value != privacy[key]:
                errors.append(f"privacy report disagrees with stage summary: {key}")
    privacy_result.update(analysis_fps=rate, frames=samples,
                          expected_samples=expected, full_duration_count=coverage,
                          independent_detection="requires_code_audit")
    return {"status": "FAIL" if errors else "PASS", "errors": errors,
            "reference_frames": ref_frames, "new_frames": new_frames,
            "reference_fps": ref_fps, "new_fps": new_fps,
            "duration_seconds": ref_frames / ref_fps,
            "reference_pixelated": len(reference["pixelated"]),
            "new_pixelated": len(new["pixelated"]), "bins": bins,
            "miss_count": len(misses), "misses": misses,
            "open_face_count": len(open_faces), "open_faces": open_faces,
            "open_lecturer_count": len(open_lecturers), "open_lecturers": open_lecturers,
            "lecturer_mode": new_blur["lecturer_mode"], "lecturer": lecturer_metrics,
            "privacy": privacy_result,
            "limitations": [
                "size_px is the shorter side of the logged pixelated rectangle, including blur margins; raw face size is unavailable",
                "privacy independence and >=16px detector sensitivity require a code audit; counters cannot prove them",
                "privacy coverage is checked from reported rate/count; summaries cannot prove sample timestamps or report provenance",
                "explicitly open identities use the actual +/-0.3s policy window; recall still requires identical pixelated frames",
            ]}


def write_report(path: str | Path, result: dict) -> None:
    destination = Path(path).expanduser().resolve()
    allowed = (ROOT / "out" / "performance").resolve()
    if (not allowed.is_relative_to(ROOT.resolve())
            or not destination.is_relative_to(allowed) or destination == allowed):
        raise ValueError("output must be a file inside this tree's ignored out/performance/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("new_decisions", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    for side in ("reference", "new"):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(f"--{side}-metrics", type=Path)
        group.add_argument(f"--{side}-db", type=Path)
    parser.add_argument("--job-id", type=int, default=1)
    parser.add_argument("--reference-job-id", type=int, default=1)
    parser.add_argument("--privacy-output", type=Path)
    parser.add_argument("--output", type=Path, help="full JSON, including every miss; only out/performance/ is writable")
    args = parser.parse_args(argv)
    try:
        reference_metrics = (read_json(args.reference_metrics) if args.reference_metrics
                             else read_stage_metrics(args.reference_db, args.reference_job_id))
        new_metrics = (read_json(args.new_metrics) if args.new_metrics
                       else read_stage_metrics(args.new_db, args.job_id))
        result = compare(read_json(args.reference), read_json(args.new_decisions), reference_metrics,
                         new_metrics, read_json(args.privacy_output) if args.privacy_output else None)
    except (ValueError, TypeError, KeyError, OverflowError, OSError, sqlite3.Error) as exc:
        result = {"status": "FAIL", "errors": [f"invalid/incomplete comparison input: {exc}"]}
    if args.output:
        try:
            write_report(args.output, result)
        except (ValueError, OSError) as exc:
            result["status"] = "FAIL"
            result["errors"].append(f"report not written: {exc}")
    # Full misses are retained in JSON output, while the terminal stays compact.
    display = {key: value for key, value in result.items()
               if key not in ("misses", "open_faces", "open_lecturers", "limitations")}
    print(json.dumps(display, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
