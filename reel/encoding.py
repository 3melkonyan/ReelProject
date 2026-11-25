"""Choose a usable H.264 encoder without touching lesson media."""

import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def h264_encoder() -> str:
    """Prefer VideoToolbox; use software when this session cannot create an encoder.

    An encoder listed by FFmpeg is not necessarily usable in a headless or
    restricted macOS session. Probe synthetic pixels once per worker process.
    A failed probe never disables blurring or alters its input.
    """
    try:
        probe = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=size=64x64:rate=1:duration=1",
                "-frames:v",
                "1",
                "-c:v",
                "h264_videotoolbox",
                "-f",
                "null",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if probe.returncode == 0:
            return "h264_videotoolbox"
    except (OSError, subprocess.SubprocessError):
        pass
    return "libx264"
