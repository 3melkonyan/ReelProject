"""C-1 benchmark: transcribe a clip (or whole file) and measure speed.

Usage:
  python tools/bench_transcribe.py LESSON.mp4 --start 1200 --duration 720 --out out/clip12
  python tools/bench_transcribe.py LESSON.mp4 --out out/full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reel.stages.transcribe import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    extract_audio,
    transcribe,
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--start", type=float)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="initial_prompt with expected technical terms ('' = none)",
    )
    a = ap.parse_args()

    before = sha256(a.src)
    wav = extract_audio(a.src, a.out / "audio.wav", a.start, a.duration)
    stats = transcribe(
        wav, a.out, model=a.model, initial_prompt=a.prompt or None, offset=a.start or 0.0
    )
    stats["source_unchanged"] = sha256(a.src) == before
    (a.out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
