"""G-40/3 item 3: AI study notes per lesson — summary, key terms, self-check questions.

Uses the project's local Ollama client (reel.llm, no paid APIs). Input is what the metadata stage already
produced (English per-part summaries + Armenian chapter titles); a short transcript excerpt is the fallback.
The local model's free Armenian is not always reliable (see reel.stages.metadata), so the answer is
schema-constrained, validated and retried once; anything unusable means "no summary", never a stopped lesson.
"""
from __future__ import annotations

import json
import re

from reel import llm

SYSTEM = (
    "You write study notes for students of an Armenian programming and design academy. "
    "Use ONLY the supplied lesson material (title, chapters, internal English summaries, transcript excerpt). "
    "Never invent topics, examples or facts that are not in the material. "
    "Write in clear, simple, grammatical EASTERN ARMENIAN (as used in Armenia), addressing students formally. "
    "Call the lecturer 'դասախոսը'; never invent names of people. "
    "Keep technical terms and code identifiers in English exactly as the material spells them, with Armenian "
    "grammar around them (term-ը, term-ի). Plain text only: no markdown. "
    "Every key term MUST literally appear in the material; never add general terms the lesson did not cover. "
    'Return JSON: {"summary": [5-10 short Armenian sentences, one idea each, in lesson order], '
    '"terms": [{"term": "key term exactly as in the material", "meaning": "Armenian explanation, max 15 words, '
    'without repeating the term"}] (3-8 items), '
    '"questions": [3-5 Armenian self-check questions a student can answer after the lesson]}.'
)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "array", "items": {"type": "string"}, "minItems": 5, "maxItems": 10},
        "terms": {"type": "array", "minItems": 3, "maxItems": 8, "items": {
            "type": "object", "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
            "required": ["term", "meaning"]}},
        "questions": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
    },
    "required": ["summary", "terms", "questions"],
}

_ARMENIAN = re.compile("[Ա-Ֆա-և]")
_BULLET = re.compile(r"^(?:[-•*·–—]+|\d{1,2}[.)]|[ա-ֆ][.)])\s+")
EXCERPT_CHARS = 9000


def _text(value: object, limit: int, *, armenian: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("`", "").replace("**", "").replace("#", "").split())
    text = _BULLET.sub("", text).strip(" -–—")
    if not text or len(text) > limit or (armenian and not _ARMENIAN.search(text)):
        return None
    from reel.stages.metadata import _normalize_terms     # same term spelling as titles and chapters
    return _normalize_terms(text)


def _grounded(term: str, source: str) -> bool:
    """The term (or each of its words longer than 3 letters) occurs in the lesson material."""
    words = [w for w in re.split(r"[\s/()-]+", term.casefold()) if len(w) > 3] or [term.casefold()]
    return term.casefold() in source or all(w in source for w in words)


def validate(data: object, material: str | None = None) -> dict:
    """Clean model output or raise ValueError(reason) for one corrective retry.
    With `material`, key terms that the lesson never mentions are dropped (the model likes to add some)."""
    if not isinstance(data, dict):
        raise ValueError("the answer must be a JSON object")

    def unique(items):
        seen, out = set(), []
        for item in items:
            key = (item["term"] if isinstance(item, dict) else item).casefold()
            if item and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    lines = []
    for item in data.get("summary") or []:
        # The model sometimes packs a whole paragraph into one item: one sentence per line instead.
        lines += re.split(r"(?<=[։.!?])\s+", item.strip()) if isinstance(item, str) and len(item) > 200 else [item]
    summary = unique(t for t in map(lambda v: _text(v, 300), lines) if t and len(t) >= 15)
    questions = unique(t for t in map(lambda v: _text(v, 220), data.get("questions") or []) if t and len(t) >= 10)
    terms = []
    for item in data.get("terms") or []:
        if isinstance(item, dict):
            term, meaning = _text(item.get("term"), 60, armenian=False), _text(item.get("meaning"), 200)
            if term and meaning:
                # "closure - ֆունկցիա, որը ..." -> "ֆունկցիա, որը ..."
                meaning = re.sub(r"^" + re.escape(term) + r"\s*[-–—:]\s*", "", meaning, flags=re.IGNORECASE)
                if material is None or _grounded(term, material):
                    if _ARMENIAN.match(meaning):          # capitalise Armenian, never code ("makeCounter")
                        meaning = meaning[:1].upper() + meaning[1:]
                    terms.append({"term": term, "meaning": meaning})
    terms = unique(terms)
    if not 5 <= len(summary):
        raise ValueError(f"summary needs 5-10 Armenian sentences, got {len(summary)} usable")
    if len(terms) < 3:
        raise ValueError(f"terms needs 3-8 items that appear in the material, with Armenian meanings; "
                         f"got {len(terms)} usable")
    if len(questions) < 3:
        raise ValueError(f"questions needs 3-5 Armenian questions, got {len(questions)} usable")
    return {"summary": summary[:10], "terms": terms[:8], "questions": questions[:5]}


def excerpt(cues: list[dict], limit: int = EXCERPT_CHARS) -> str:
    """Evenly spread transcript sample (beginning, middle, end) that fits the model context."""
    lines = [c["text"] for c in cues if c.get("text")]
    if sum(len(x) + 1 for x in lines) <= limit:
        return "\n".join(lines)
    step, out, size = max(1, len(lines) // max(1, limit // 120)), [], 0
    for line in lines[::step]:
        if size + len(line) + 1 > limit:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def material(meta: dict, parts: list, transcript: str) -> str:
    chapters = [c.get("title") for c in meta.get("chapters") or [] if isinstance(c, dict) and c.get("title")]
    english = [{"topic": p.get("topic"), "summary": p.get("summary")} for p in parts or []
               if isinstance(p, dict) and isinstance(p.get("summary"), str)]
    return json.dumps({"title": meta.get("title"), "chapters": chapters, "internal_summaries": english,
                       "transcript_excerpt": "" if len(english) >= 2 else transcript}, ensure_ascii=False)


def generate(meta: dict, parts: list, transcript: str, *, chat=None, model: str | None = None) -> dict:
    """Raises llm.LLMError (model unavailable) or ValueError (unusable twice)."""
    chat = chat or llm.chat_json
    payload, feedback = material(meta, parts, transcript), ""
    for _ in range(2):
        data = chat(SYSTEM + feedback, payload, model=model or llm.DEFAULT_MODEL, schema=SCHEMA, temperature=0.2)
        try:
            return validate(data, payload.casefold())
        except ValueError as error:
            feedback = f" Your previous answer was rejected: {error}. Follow the JSON format and rules exactly."
    raise ValueError(f"AI summary unusable: {feedback.strip()}")
