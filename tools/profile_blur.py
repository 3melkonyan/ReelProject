"""Profile blur and an independent final privacy scan on a local video.

    python tools/profile_blur.py stage --video /path/to/excerpt.mp4 \
        --out-dir out/performance/reference --with-privacy

All generated media and measurements stay under ignored out/performance/.
No application configuration, database, credentials or worker is used.
Pipe times measure blocking IO; child CPU and lifetimes overlap Python work.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "performance"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Measurements:
    """Nested wall/process CPU timers; exclusive values subtract timed children."""

    def __init__(self, wall=time.perf_counter, cpu=time.process_time, progress=None):
        self.wall, self.cpu = wall, cpu
        self.progress = progress
        self.local = threading.local()
        self.lock = threading.Lock()
        self.rows = defaultdict(lambda: defaultdict(float))
        self.children = []
        self.analyses = []
        self.threads = set()

    def stack(self):
        if not hasattr(self.local, "stack"):
            self.local.stack = []
        return self.local.stack

    def phase(self):
        return (
            "/".join(r["name"][6:] for r in self.stack() if r["name"].startswith("phase."))
            or "setup"
        )

    @contextmanager
    def measure(self, name, *, phase=None):
        stack = self.stack()
        parent_phase = self.phase() if phase is None else phase
        phase = (
            ("" if parent_phase == "setup" else parent_phase + "/") + name[6:]
            if name.startswith("phase.")
            else parent_phase
        )
        row = dict(name=name, wall=self.wall(), cpu=self.cpu(), child_wall=0.0, child_cpu=0.0)
        stack.append(row)
        if self.progress and name.startswith("phase."):
            self.progress({"phase": phase, "event": "started"})
        try:
            yield
        finally:
            elapsed, cpu = self.wall() - row["wall"], self.cpu() - row["cpu"]
            stack.pop()
            if stack:
                stack[-1]["child_wall"] += elapsed
                stack[-1]["child_cpu"] += cpu
            with self.lock:
                self.threads.add(threading.get_ident())
                result = self.rows[(phase, name)]
                result["calls"] += 1
                result["wall_inclusive_sec"] += elapsed
                result["wall_exclusive_sec"] += max(0.0, elapsed - row["child_wall"])
                result["parent_cpu_inclusive_sec"] += cpu
                result["parent_cpu_exclusive_sec"] += max(0.0, cpu - row["child_cpu"])
            if self.progress and name.startswith("phase."):
                self.progress({"phase": phase, "event": "finished", "wall_sec": round(elapsed, 3)})

    def timed(self, name, fn):
        @wraps(fn)
        def call(*args, **kwargs):
            with self.measure(name):
                return fn(*args, **kwargs)

        return call

    def report(self):
        phases = {
            phase: row["wall_inclusive_sec"]
            for (phase, name), row in self.rows.items()
            if name == "phase." + phase.rsplit("/", 1)[-1]
        }
        metrics = []
        for (phase, name), row in sorted(self.rows.items()):
            denominator = phases.get(phase, 0.0)
            metrics.append(
                {
                    "phase": phase,
                    "metric": name,
                    **{k: int(v) if k == "calls" else round(v, 6) for k, v in row.items()},
                    "exclusive_percent_of_phase": round(
                        100 * row["wall_exclusive_sec"] / denominator, 3
                    )
                    if denominator
                    else None,
                }
            )
        return {
            "metrics": metrics,
            "children": self.children,
            "instrumented_python_threads": len(self.threads),
        }


class Pipe:
    """Delegate all IO semantics while measuring blocking pipe operations."""

    def __init__(self, stream, stats, role):
        self.stream, self.stats, self.role = stream, stats, role
        self.phase = stats.phase()

    def __getattr__(self, name):
        value = getattr(self.stream, name)
        if name in {"read", "readinto", "read1", "write", "flush", "close"}:

            @wraps(value)
            def measured(*args, **kwargs):
                with self.stats.measure("ffmpeg." + self.role + ".pipe_" + name, phase=self.phase):
                    return value(*args, **kwargs)

            return measured
        return value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def popen_class(original, stats):
    """Use wait4 to record each reaped child's CPU, including concurrent ffmpegs.

    Popen.poll normally calls waitpid directly; both paths must use wait4 or a
    short-lived ffprobe may lose its per-process CPU record before wait().
    """

    class ProfilePopen(original):
        def __init__(self, args, *pos, **kwargs):
            command = [str(a) for a in args] if not isinstance(args, str) else [args]
            self._profile_role = (
                ("encode" if kwargs.get("stdin") == subprocess.PIPE else "decode")
                if command and Path(command[0]).name == "ffmpeg"
                else "probe"
            )
            self._profile_phase = stats.phase()
            self._profile_started = time.perf_counter()
            self._profile_recorded = False
            with stats.measure("process." + self._profile_role + ".start"):
                super().__init__(args, *pos, **kwargs)
            if self._profile_role != "probe":
                if self.stdout is not None:
                    self.stdout = Pipe(self.stdout, stats, self._profile_role)
                if self.stdin is not None:
                    self.stdin = Pipe(self.stdin, stats, self._profile_role)

        def _profile_waitpid(self, flags):
            pid, status, usage = os.wait4(self.pid, flags)
            if pid and not self._profile_recorded:
                self._profile_recorded = True
                elapsed = time.perf_counter() - self._profile_started
                cpu = usage.ru_utime + usage.ru_stime
                with stats.lock:
                    stats.children.append(
                        {
                            "phase": self._profile_phase,
                            "role": self._profile_role,
                            "pid": self.pid,
                            "lifetime_wall_sec": round(elapsed, 6),
                            "cpu_user_sec": round(usage.ru_utime, 6),
                            "cpu_system_sec": round(usage.ru_stime, 6),
                            "cpu_sec": round(cpu, 6),
                            "average_cores_over_lifetime": round(cpu / elapsed, 4)
                            if elapsed
                            else 0.0,
                        }
                    )
            return pid, status

        def _try_wait(self, wait_flags):
            try:
                return self._profile_waitpid(wait_flags)
            except ChildProcessError:
                return self.pid, 0

        def _internal_poll(self, _deadstate=None):
            if self.returncode is None and self._waitpid_lock.acquire(False):
                try:
                    if self.returncode is not None:
                        return self.returncode
                    try:
                        pid, status = self._profile_waitpid(os.WNOHANG)
                        if pid == self.pid:
                            self._handle_exitstatus(status)
                    except ChildProcessError:
                        self.returncode = 0 if _deadstate is None else _deadstate
                finally:
                    self._waitpid_lock.release()
            return self.returncode

        def wait(self, *args, **kwargs):
            with stats.measure("ffmpeg." + self._profile_role + ".wait"):
                return super().wait(*args, **kwargs)

    return ProfilePopen


@contextmanager
def instrument(stats):
    """Patches live only inside this process/context and restore even on errors."""
    import cv2

    from reel import blur, lecturer
    from reel.stages import privacy

    original_faceid = privacy.FaceID
    original_analyze = blur.analyze

    def analyze(*args, **kwargs):
        result = original_analyze(*args, **kwargs)
        stats.analyses.append((stats.phase(), result))
        return result

    class ProfileFaceID(original_faceid):
        # Override Python entry points: OpenCV's C detector/recognizer methods
        # themselves are read-only attributes and cannot safely be monkeypatched.
        def __init__(self, *args, **kwargs):
            with stats.measure("faceid.load"):
                super().__init__(*args, **kwargs)

        def faces(self, *args, **kwargs):
            with stats.measure("yunet"):
                return super().faces(*args, **kwargs)

        def feature(self, *args, **kwargs):
            with stats.measure("sface.feature_align"):
                return super().feature(*args, **kwargs)

        def best_match(self, *args, **kwargs):
            with stats.measure("sface.match"):
                return super().best_match(*args, **kwargs)

    with ExitStack() as patches:
        patches.enter_context(patch.object(blur, "analyze", analyze))
        for module in (blur, lecturer, privacy):
            patches.enter_context(patch.object(module, "FaceID", ProfileFaceID))
        patches.enter_context(
            patch.object(subprocess, "Popen", popen_class(subprocess.Popen, stats))
        )
        patches.enter_context(patch.object(cv2, "resize", stats.timed("resize", cv2.resize)))
        for obj, name, label in [
            (blur._Tracker, "update", "tracking"),
            (blur._LecturerBoxes, "matches", "tracking.policy"),
            (blur, "_retained", "tracking.hold"),
            (blur, "_classify_analysis", "tracking.classify"),
            (blur, "_pixelate", "pixelate"),
            (blur, "_identify", "identify"),
            (blur, "_detections", "detections"),
            (blur, "_write_decisions", "write_decisions"),
            (privacy, "judge", "privacy.judge"),
            (privacy, "_save_crops", "privacy.save_crops"),
            (blur, "analyze", "phase.analyze"),
            (blur, "blur_faces", "phase.blur_frames"),
            (lecturer, "discover", "phase.lecturer_discover"),
            (lecturer, "track_refs", "phase.track_refs"),
            (lecturer, "relabel", "phase.relabel"),
            (privacy, "scan", "phase.privacy"),
        ]:
            patches.enter_context(patch.object(obj, name, stats.timed(label, getattr(obj, name))))
        yield


def safe_output(path, root=None):
    if root is None:
        root = OUT
        if not root.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Output directory must stay inside this worktree's out/performance")
    resolved = Path(path).expanduser().resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise ValueError("All generated outputs must stay inside this worktree's out/performance")
    return resolved


def save_json(path, value):
    safe_output(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def serializable_analysis(records):
    return [{k: v for k, v in r.items() if k != "feat"} for r in records]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=("stage", "analyze", "blur", "privacy"))
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--refs", type=Path, action="append", default=[])
    ap.add_argument("--analysis", type=Path, help="analysis JSON required by blur mode")
    ap.add_argument(
        "--decisions", type=Path, help="blur decisions used by independent privacy scan"
    )
    ap.add_argument("--job-id", type=int, default=1)
    ap.add_argument(
        "--fps", type=float, default=5.0, help="analyze FPS; privacy uses its production default"
    )
    ap.add_argument(
        "--with-privacy", action="store_true", help="scan completed stage/blur output independently"
    )
    ap.add_argument(
        "--opencv-threads", type=int, help="explicit experiment only; omit for production default"
    )
    args = ap.parse_args(argv)
    out = safe_output(args.out_dir)
    if out.exists() and any(out.iterdir()):
        ap.error("--out-dir must be empty; use a fresh directory for each measurement")
    out.mkdir(parents=True, exist_ok=True)
    # tempfile is process-local; production's anonymous ffmpeg logs also stay here.
    with tempfile.TemporaryDirectory(prefix="profile-tmp-", dir=out) as temporary:
        previous_tmp = tempfile.tempdir
        tempfile.tempdir = temporary
        try:
            return run(args, out)
        finally:
            tempfile.tempdir = previous_tmp


def run(args, out):
    import cv2

    import reel.stages as stages
    from reel import blur
    from reel.stages import privacy

    if args.mode == "blur" and args.analysis is None:
        raise ValueError("blur mode requires --analysis to isolate the every-frame pass")
    metadata = {}
    # Supply an isolated automatic-lecturer context without application state.
    metadata["render"] = {**metadata.get("render", {}), "final": str(args.video.resolve())}
    metadata.setdefault("segments", {})
    context = SimpleNamespace(
        db=None,
        job_id=args.job_id,
        source=args.video,
        workdir=out,
        group="benchmark",
        config={"groups": {"benchmark": {"lecturers": []}}},
        _outputs=metadata,
        out=lambda name: metadata.get(name, {}),
    )
    stats = Measurements(
        progress=lambda event: print(json.dumps(event), file=sys.stderr, flush=True)
    )
    output = {
        "schema_version": 1,
        "label": args.label,
        "mode": args.mode,
        "input": str(args.video.resolve()),
        "out_dir": str(out),
        "analysis_fps_requested": args.fps,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "logical_cpus": os.cpu_count(),
        "opencv_threads_previous": cv2.getNumThreads(),
        "opencv_threads": cv2.getNumThreads(),
        "timing_definitions": {
            "wall_inclusive_sec": "elapsed wall time including instrumented children",
            "wall_exclusive_sec": "elapsed wall minus nested instrumented calls; percentages use owning phase wall",
            "parent_cpu_sec": "process CPU includes OpenCV threads; excludes ffmpeg children",
            "child_cpu_sec": "wait4 user+system CPU for each child; no wall-time summing with parent",
            "pipe": "blocking pipe IO/copy and wait are stall costs, not isolated decoder/encoder compute",
            "analyze_resize": "ffmpeg scale is inside decoder child CPU; cannot separate scale from decode with this wrapper",
            "pixelate": "pixelate inclusive includes nested resize; exclusive excludes those resize calls",
        },
        "caveats": [
            "Per-frame/per-face timers and process_time calls add profiling overhead; compare like-for-like instrumented runs.",
            "Decoder and encoder run concurrently with Python: child lifetimes and CPU must not be added to wall buckets.",
            "Background pipe timers retain their creating phase; their wall/CPU can overlap the caller and must not be summed into total wall.",
            "Phase timers nest: privacy includes a fresh analyze; blur_frames can include analyze when not supplied.",
            "process CPU inside method timers is exact only without overlapping Python worker calls; aggregate run CPU remains valid.",
            "Hardware VideoToolbox service CPU/GPU time outside the child is not attributed by wait4.",
        ],
    }
    previous_threads = cv2.getNumThreads()
    if args.opencv_threads is not None:
        cv2.setNumThreads(args.opencv_threads)
        output["opencv_threads"] = cv2.getNumThreads()
    started, cpu_started = time.perf_counter(), time.process_time()
    children_started = resource.getrusage(resource.RUSAGE_CHILDREN)
    error = None
    try:
        with instrument(stats), stats.measure("phase.run"):
            refs = [str(p.resolve()) for p in args.refs]
            if args.mode == "stage":
                result = stages.blur(context)
                if stats.analyses:
                    save_json(out / "analysis.json", serializable_analysis(stats.analyses[-1][1]))
                save_json(out / "blur_stage.json", result)
                output["blur"] = result
                refs = result["refs_dirs"]
            elif args.mode == "analyze":
                analysis = blur.analyze(str(args.video), refs, fps=args.fps)
                save_json(out / "analysis.json", serializable_analysis(analysis))
                output["analysis"] = {
                    "faces": len(analysis),
                    "lecturer_faces": sum(bool(a["lecturer"]) for a in analysis),
                }
            elif args.mode == "blur":
                analysis = json.loads(args.analysis.read_text())
                result = blur.blur_faces(
                    str(args.video),
                    str(out / "final_blurred.mp4"),
                    refs,
                    analysis=analysis,
                    decisions_path=str(out / "blur_decisions.json"),
                )
                result = {
                    **result,
                    "final": str(out / "final_blurred.mp4"),
                    "decisions": str(out / "blur_decisions.json"),
                    "refs_dirs": refs,
                }
                save_json(out / "blur_stage.json", result)
                output["blur"] = result
            if args.mode == "privacy" or args.with_privacy:
                result = output.get("blur", {})
                video = result.get("final", str(args.video))
                decisions = result.get("decisions", str(args.decisions) if args.decisions else None)
                output["privacy"] = privacy.scan(video, refs, out / "privacy", decisions=decisions)
    except BaseException as exc:
        error = exc
        output["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        elapsed, cpu = time.perf_counter() - started, time.process_time() - cpu_started
        child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        child_cpu = (
            child_usage.ru_utime
            - children_started.ru_utime
            + child_usage.ru_stime
            - children_started.ru_stime
        )
        output.update(stats.report())
        output["totals"] = {
            "wall_sec": round(elapsed, 6),
            "parent_cpu_sec": round(cpu, 6),
            "children_cpu_sec": round(child_cpu, 6),
            "average_parent_cores": round(cpu / elapsed, 4),
            "average_total_cores": round((cpu + child_cpu) / elapsed, 4),
            "cpu_percent_one_core_100": round(100 * (cpu + child_cpu) / elapsed, 2),
        }
        phase_cpu = defaultdict(float)
        for child in stats.children:
            phase_cpu[child["phase"]] += child["cpu_sec"]
        for metric in output["metrics"]:
            if metric["metric"] == "phase." + metric["phase"].rsplit("/", 1)[-1]:
                wall = metric["wall_inclusive_sec"]
                metric["direct_children_cpu_sec"] = round(phase_cpu[metric["phase"]], 6)
                subtree_cpu = sum(
                    value
                    for name, value in phase_cpu.items()
                    if name == metric["phase"] or name.startswith(metric["phase"] + "/")
                )
                metric["children_cpu_inclusive_sec"] = round(subtree_cpu, 6)
                metric["average_parent_cores"] = (
                    round(metric["parent_cpu_inclusive_sec"] / wall, 4) if wall else 0.0
                )
                metric["average_total_cores"] = (
                    round((metric["parent_cpu_inclusive_sec"] + subtree_cpu) / wall, 4)
                    if wall
                    else 0.0
                )
        save_json(out / "profile.json", output)
        cv2.setNumThreads(previous_threads)
        print(
            json.dumps(
                {
                    "profile": str(out / "profile.json"),
                    "totals": output["totals"],
                    "error": output.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    if error:
        raise error
    return output


if __name__ == "__main__":
    main()
