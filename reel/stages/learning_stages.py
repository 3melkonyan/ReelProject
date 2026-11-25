"""Pipeline stages. None of them ever stops a lesson: a failure becomes a note (automation first)."""

from __future__ import annotations

import time
from datetime import UTC

from reel.pipeline import Ctx

CAPTION_RETRY_WAIT = (0, 60, 180)  # seconds before each YouTube caption attempt (transient errors)


def captions_stage(ctx: Ctx) -> dict:
    # Caption storage and adapters are optional too, not only the final upload request.
    try:
        return _captions_stage(ctx)
    except Exception as error:
        return {
            "skipped": type(error).__name__,
            "auto_note": "captions unavailable — published without them",
        }


def _captions_stage(ctx: Ctx) -> dict:
    """Armenian captions: Whisper text on the edited timeline -> lesson_cues (search, synced transcript),
    work/<id>/captions/captions.hy.{srt,vtt} and a published YouTube caption track."""
    from reel import captions, learning, stages

    cues, source = learning.transcript_cues(ctx.db, ctx.job_id, ctx.workdir.parent)
    learning.save_cues(ctx.db, ctx.job_id, cues, source)
    if not cues:
        return {
            "skipped": "no transcript cues",
            "source": source,
            "auto_note": "captions skipped — the transcript is unavailable",
        }
    parts = captions.split_cues(cues)
    folder = ctx.workdir / "captions"
    folder.mkdir(parents=True, exist_ok=True)
    language = ctx.config.get("transcription_language", "hy")
    if language == "auto":
        import json

        data = json.loads((ctx.workdir / "transcript" / "transcript.json").read_text())
        language = data.get("language") or "hy"
    srt, vtt = folder / f"captions.{language}.srt", folder / f"captions.{language}.vtt"
    srt.write_text(captions.to_srt(parts), encoding="utf-8")
    vtt.write_text(captions.to_vtt(parts), encoding="utf-8")
    result = {
        "cues": len(cues),
        "caption_cues": len(parts),
        "source": source,
        "srt": str(srt),
        "vtt": str(vtt),
    }
    video = ctx.out("youtube_upload").get("external_id")
    if not video:
        return {**result, "skipped": "no YouTube video"}
    youtube = stages._pub("youtube")

    def find():
        found = youtube.find_captions(video, language)
        return (found, None) if found else None

    error = None
    for wait in CAPTION_RETRY_WAIT:
        time.sleep(wait)
        try:
            r = ctx.publish_once(
                "captions", lambda: (youtube.upload_captions(video, str(srt), language), None), find
            )
            return {
                **result,
                "uploaded": True,
                "caption_id": r["external_id"],
                "reused": r["reused"],
            }
        except Exception as e:  # noqa: BLE001 - captions must never block the lesson
            error = e
            if not getattr(e, "retryable", True):
                break
    return {
        **result,
        "uploaded": False,
        "error": type(error).__name__,
        "auto_note": f"captions not uploaded to YouTube ({type(error).__name__}) — published without them",
    }


def summary_stage(ctx: Ctx) -> dict:
    """AI study notes (summary, key terms, self-check questions) from the metadata stage's material.
    The local model may be off or give unusable text: then the lesson simply has no summary."""
    import json
    from datetime import datetime

    from reel import learning, lesson_summary, llm

    try:
        parts = (
            json.loads((ctx.workdir / "metadata.json").read_text(encoding="utf-8")).get("parts")
            or []
        )
    except (OSError, ValueError, AttributeError):
        parts = []
    try:
        cues, _ = learning.transcript_cues(ctx.db, ctx.job_id, ctx.workdir.parent)
        notes = lesson_summary.generate(
            ctx.out("metadata"),
            parts,
            lesson_summary.excerpt(cues),
            **({"language": "en"} if ctx.config.get("metadata_language", "hy") == "en" else {}),
        )
    except Exception as e:  # noqa: BLE001 - LLMError, unusable text twice, anything else: never stop
        return {
            "skipped": f"{type(e).__name__}: {str(e)[:200]}",
            "auto_note": "AI summary skipped — the lesson is published without it",
        }
    return {
        **notes,
        "model": llm.DEFAULT_MODEL,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def install(stages: list) -> None:
    """Insert optional learning stages after their required inputs are available."""

    def after(anchor: str, entry: tuple) -> None:
        names = [name for name, _ in stages]
        if entry[0] not in names:
            stages.insert(names.index(anchor) + 1 if anchor in names else len(stages), entry)

    after("metadata", ("summary", summary_stage))  # the model is still loaded right after metadata
    after("thumbnail_set", ("captions", captions_stage))
