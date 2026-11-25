"""Audio extraction and transcription (MLX Whisper). Never modifies the source file."""
from __future__ import annotations

import json
import subprocess
from reel.progress import observed_run as _observed_run

def observed_run(*args, **kwargs):
    return _observed_run(*args, run=subprocess.run, **kwargs)

import time
from pathlib import Path

DEFAULT_MODEL = "mlx-community/whisper-large-v3-mlx"
# Measured on a real lesson (2026-11-17): nudges English terms to Latin script (e.g. "C++").
DEFAULT_PROMPT = ("Դասախոսությունը ծրագրավորման մասին է. JavaScript, C++, function, inline, compiler, "
                  "class, object, this, context, prototype, constructor, method, code, console.log, return.")


def extract_audio(src: str | Path, dst: str | Path,
                  start: float | None = None, duration: float | None = None) -> Path:
    """Write 16 kHz mono WAV (what Whisper expects) to dst. Source is read-only."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)]
    observed_run(cmd, check=True)
    return dst


def media_duration(path: str | Path) -> float:
    out = observed_run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def release_model() -> None:
    """Free the cached Whisper weights (~3 GB) right after transcription. mlx_whisper keeps them in
    ModelHolder for the life of the worker, i.e. through blur and the 8-11 GB LLM stages (measured:
    worker at 8.9 GB, memory pressure 'warn'). Reloading for the next lesson takes seconds."""
    try:
        import gc
        import sys
        holder = sys.modules["mlx_whisper.transcribe"].ModelHolder
        holder.model = holder.model_path = None
        gc.collect()
        import mlx.core as mx
        clear = getattr(mx, "clear_cache", None) or mx.metal.clear_cache
        clear()
    except Exception:  # noqa: BLE001 - memory hygiene must never fail the stage
        pass


def transcribe(audio: str | Path, out_dir: str | Path, *, model: str = DEFAULT_MODEL,
               language: str | None = "hy", initial_prompt: str | None = DEFAULT_PROMPT,
               offset: float = 0.0) -> dict:
    """Transcribe audio -> out_dir/transcript.{txt,json}. Returns summary dict.

    offset: added to all timestamps (when audio is a clip of a longer file).
    """
    import mlx_whisper  # heavy import, keep lazy

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_sec = media_duration(audio)

    t0 = time.monotonic()
    try:
        from reel.progress import whisper_observer
        with whisper_observer(mlx_whisper.transcribe):
            result = mlx_whisper.transcribe(
                str(audio),
                path_or_hf_repo=model,
                language=language,
                initial_prompt=initial_prompt,
                condition_on_previous_text=False,  # reduces repetition loops on long audio
                verbose=None,
            )
    finally:
        release_model()
    elapsed = time.monotonic() - t0

    segments = [
        {"start": round(s["start"] + offset, 2), "end": round(s["end"] + offset, 2),
         "text": s["text"].strip()}
        for s in result["segments"]
    ]
    (out_dir / "transcript.json").write_text(
        json.dumps({"language": language or result.get("language"), "model": model, "segments": segments},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "transcript.txt").write_text(
        "\n".join(s["text"] for s in segments if s["text"]) + "\n", encoding="utf-8")

    return {"audio_sec": round(audio_sec, 1), "elapsed_sec": round(elapsed, 1),
            "speed_x": round(audio_sec / elapsed, 1) if elapsed else None,
            "segments": len(segments), "model": model}
