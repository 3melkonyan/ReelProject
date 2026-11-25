"""Small best-effort dashboard preview. The caller must pass the blurred output."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from reel.progress import observed_run as _observed_run

HEIGHT = 480
PRESET = "fast"
CRF = 30
MAXRATE = "120k"
BUFSIZE = "240k"
AUDIO_RATE = "48k"
VBV_INIT = 0.25


def observed_run(*args, **kwargs):
    return _observed_run(*args, run=subprocess.run, **kwargs)


def make_preview(src: str | Path, out: str | Path) -> dict:
    """Encode at low priority, atomically retaining an existing preview on failure."""
    return _encode(src, out)


def _encode(
    src: str | Path, out: str | Path, *, start: float = 0, seconds: float | None = None
) -> dict:
    """The evaluator can bound a real-source trial without another video encoding."""
    src, out = Path(src), Path(out)
    part = None
    started = time.monotonic()
    try:
        if not src.is_file() or src.resolve() == out.resolve():
            raise ValueError("invalid preview paths")
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=out.stem + ".", suffix=".part.mp4", dir=out.parent)
        os.close(fd)
        part = Path(name)
        scale = f"scale=-2:'min({HEIGHT},trunc(ih/2)*2)':flags=lanczos,setsar=1"
        seek = ["-ss", str(start)] if start else []
        length = ["-t", str(seconds)] if seconds is not None else []
        observed_run(
            [
                "nice",
                "-n",
                "10",
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-threads",
                "2",
                *seek,
                "-i",
                str(src),
                *length,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-preset",
                PRESET,
                "-crf",
                str(CRF),
                "-maxrate",
                MAXRATE,
                "-bufsize",
                BUFSIZE,
                "-x264-params",
                f"vbv-init={VBV_INIT}",
                "-pix_fmt",
                "yuv420p",
                "-g",
                "300",
                "-sc_threshold",
                "0",
                "-force_key_frames",
                "expr:gte(t,n_forced*10)",
                "-c:a",
                "aac",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-b:a",
                AUDIO_RATE,
                "-threads",
                "4",
                "-filter_threads",
                "2",
                "-movflags",
                "+faststart",
                str(part),
            ],
            check=True,
            capture_output=True,
            timeout=3600,
        )
        if not part.is_file() or not part.stat().st_size:
            raise ValueError("empty preview")
        size = part.stat().st_size
        part.replace(out)
        return {
            "path": str(out),
            "size_mb": round(size / 1e6, 3),
            "size_bytes": size,
            "elapsed_sec": round(time.monotonic() - started, 3),
        }
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"skipped": "Preview unavailable (" + type(error).__name__ + ")"}
    finally:
        if part is not None:
            part.unlink(missing_ok=True)
