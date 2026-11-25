"""G-40/3: Armenian captions (SRT/VTT) from the Whisper transcript, on the FINAL (cut) video timeline.

Pure functions only. Transcript times are on the original (joined) recording; `keep` maps them to the
edited video exactly like the chapters do (reel.timeline.to_final).
"""
from __future__ import annotations

import math
import re

from reel import timeline

MAX_CHARS = 84          # two caption lines of ~42 characters
LINE_CHARS = 42
MAX_SEC = 7.0
MIN_KEPT = 0.5          # a sentence that is mostly inside a cut is dropped, not squeezed in

# Armenian emphasis/exclamation/question/abbreviation marks sit INSIDE words (ինչո՞ւ): drop them for search.
_MARKS = dict.fromkeys(map(ord, "՛՜՞՟"), None)
# Phrases Whisper invents on silence or music (not said in a lesson).
_HALLUCINATIONS = re.compile(
    r"^(?:շնորհակալություն դիտելու համար|субтитры.*|продолжение следует.*|thank(?:s| you) for watching.*|"
    r"[\s.։,…♪-]*)$")


def norm(text: object) -> str:
    """Search form: case-folded (և -> եւ), Armenian in-word marks removed, single spaces."""
    return " ".join(str(text or "").translate(_MARKS).casefold().split())


def _clean(text: object) -> str:
    return " ".join(str(text or "").replace("-->", "→").split())


def final_cues(segments: list[dict], keep=None) -> list[dict]:
    """Transcript segments -> [{'start','end','text'}] on the final timeline.

    keep=None means the segments are already on the final timeline (G-40/2 transcript_final.json).
    """
    intervals = [(float(k[0]), float(k[1])) for k in keep] if keep is not None else None
    cues: list[dict] = []
    for segment in segments or []:
        try:
            a, b = float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = _clean(segment.get("text"))
        if not text or not math.isfinite(a) or not math.isfinite(b) or b <= a:
            continue
        if _HALLUCINATIONS.match(norm(text)) or (cues and norm(cues[-1]["text"]) == norm(text)):
            continue                                   # silence phrase or a repetition loop
        if intervals is not None:
            parts = [(max(a, x), min(b, y)) for x, y in intervals if min(b, y) > max(a, x)]
            if sum(y - x for x, y in parts) < MIN_KEPT * (b - a):
                continue
            a, b = timeline.to_final(parts[0][0], intervals), timeline.to_final(parts[-1][1], intervals)
        if cues and a < cues[-1]["end"]:              # Whisper overlaps: never show two cues at once
            if a - cues[-1]["start"] >= 0.3:
                cues[-1]["end"] = round(a, 2)
            else:
                a = cues[-1]["end"]
        if b - a >= 0.2:
            cues.append({"start": round(a, 2), "end": round(b, 2), "text": text})
    return cues


def split_cues(cues: list[dict], max_chars: int = MAX_CHARS, max_sec: float = MAX_SEC) -> list[dict]:
    """Long Whisper segments -> readable caption cues; time is shared in proportion to text length."""
    out = []
    for cue in cues:
        words, duration = cue["text"].split(), cue["end"] - cue["start"]
        # Split by length; by duration only while every piece keeps ~32+ characters (no one-word cues).
        by_time = min(math.ceil(duration / max_sec), len(cue["text"]) // 32)
        count = min(len(words), max(1, math.ceil(len(cue["text"]) / max_chars), by_time))
        if count <= 1:
            out.append(dict(cue))
            continue
        target, chunks, current = len(cue["text"]) / count, [], []
        for word in words:
            size = len(" ".join(current))
            if current and (size + 1 + len(word) > max_chars or (size >= target and len(chunks) < count - 1)):
                chunks.append(" ".join(current))
                current = []
            current.append(word)
        chunks.append(" ".join(current))
        total, start = sum(len(c) for c in chunks), cue["start"]
        for index, chunk in enumerate(chunks):
            end = cue["end"] if index == len(chunks) - 1 else start + duration * len(chunk) / total
            out.append({"start": round(start, 2), "end": round(end, 2), "text": chunk})
            start = end
    return out


def wrap(text: str, width: int = LINE_CHARS) -> list[str]:
    """At most two lines, broken at the space nearest the middle."""
    if len(text) <= width or " " not in text:
        return [text]
    middle = len(text) / 2
    cut = min((i for i, c in enumerate(text) if c == " "), key=lambda i: abs(i - middle))
    return [text[:cut], text[cut + 1:]]


def _stamp(seconds: float, separator: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        lines = wrap(_clean(cue["text"]).replace("<", "‹").replace(">", "›"))
        blocks.append(f"{index}\n{_stamp(cue['start'], ',')} --> {_stamp(cue['end'], ',')}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def to_vtt(cues: list[dict]) -> str:
    def escape(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    blocks = [f"{_stamp(c['start'], '.')} --> {_stamp(c['end'], '.')}\n" + "\n".join(wrap(escape(_clean(c["text"]))))
              for c in cues]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")
