"""Title, description, chapters from the transcript (local LLM, map -> reduce).

Chapter times are computed on ORIGINAL timestamps and mapped through the cut
timeline, so they match the final video.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from reel import llm, timeline
from reel.pipeline import StopJob
from reel.stages.segments import fmt

PART_SEC = 600  # summarise in 10-minute parts

MAP_SYS = (
    "Extract precise technical topics from an imperfect Armenian programming transcript. "
    "Write this INTERNAL summary in ENGLISH; a separate stage writes the Armenian publication. "
    "Recover standard programming names from phonetic ASR only when supported by context. "
    "Canonical spellings include this, function declaration, function expression, arrow function, "
    "lexical scope, dynamic this, closure, call, apply, bind, compiler, bytecode, interpreter. "
    "Never copy Armenian transliterations or invent technical terms. Ignore unintelligible fragments. "
    "Find 2-4 distinct genuine topic transitions per part, with specific identifiers/mechanisms "
    "instead of generic function creation/context. Use only the supplied absolute timestamps. "
    'Return JSON: {"topic": "specific main topic, <=8 words", "summary": "2-3 precise sentences", '
    '"topic_changes": [{"t": <absolute seconds>, "topic": "specific technical subject, <=8 words"}]}.'
)

REDUCE_SYS = (
    "Write publication text for a programming lesson using the supplied English summaries and FIXED chapter_plan. "
    "Write a specific natural ARMENIAN title naming 2-3 central technical subjects, preferably under 70 characters. "
    "No clickbait, vague promises, 'աշխարհը', 'գաղտնիքները', 'Հետադիտում' or generic overview titles. "
    "Example: 'this-ի կանչը, lexical scope-ը և Event Loop-ը'. "
    "Write 3-6 Armenian description sentences explaining the actual lesson. "
    "For EVERY chapter ID c0, c1, etc., write a SHORT NOUN PHRASE of ONLY 2-4 words naming that chapter's "
    "specific mechanism or example, grounded in source_topic/context. NEVER write explanatory sentences. "
    "Each plan entry gives detail_max_chars: use complete words and finish well BEFORE that limit; "
    "never cut words to fit. Avoid sentence verbs such as է, են, կարող and պետք. "
    "Examples: 'կանչի արժեքի որոշումը', 'հիշողության հասցեի պահպանումը', 'հերթի աշխատանքի կարգը'. "
    "The fixed English topic is prepended by code; "
    "do not repeat it, number chapters, change topics or provide timestamps. "
    "Do not write generic repeated captions such as Ֆունկցիայի ստեղծում or Ֆունկցիայի համատեքստ. "
    "Keep all programming terms/code identifiers in English: function, this, hoisting, lexical scope, "
    "call, apply, bind, bytecode, interpreter. Never transliterate them into Armenian. "
    "Use Armenian grammar around technical terms. Plain text only: no markdown, backticks or asterisks. "
    'Return {"title": "Armenian lesson title", "description": "Armenian lesson description", '
    '"chapter_details": {"c0": "specific Armenian explanation", "c1": "specific Armenian explanation", ...}}.'
)


# Free-form Armenian from the local model (gemma3:12b) is unreliable: real run 2026-11-18 produced
# invented Armenian words. Default = grounded text built from the lesson's
# own topics (correct, a bit dry). REEL_LLM_TEXT=1 re-enables model-written title/description/details.
LLM_TEXT = os.environ.get("REEL_LLM_TEXT") == "1"

HARD_MIN_CHAPTERS = 3   # YouTube shows chapters from 3; a lesson with fewer distinct topics than
                        # the preferred minimum is still published (metadata is not a safety stop)


def _chapter_limits(total: float) -> tuple[int, int]:
    """(preferred minimum, maximum) chapter counts; the hard floor is HARD_MIN_CHAPTERS."""
    if total >= 90 * 60:
        return 8, 15
    if total >= 40 * 60:
        return 6, 10
    return 3, 8


def _reduce_schema(plan: list[dict]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["title", "description", "chapter_details"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 90},
            "description": {"type": "string", "minLength": 1},
            "chapter_details": {
                "type": "object", "additionalProperties": False,
                "required": [c["id"] for c in plan],
                "properties": {c["id"]: {"type": "string", "minLength": 1,
                                         "maxLength": _detail_limit(c["topic"]),
                                         "pattern": r"^\S+(?: \S+){1,3}$"}
                               for c in plan},
            },
        },
    }


def _detail_limit(topic: str) -> int:
    return min(48, 72 - len(topic))


_GENERIC_HEADS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "with", "vs", "versus",
    "javascript", "function", "functions", "introduction", "introducing", "overview", "review",
    "understanding", "exploring", "discussion", "discussing", "using", "use", "basic", "basics",
    "advanced", "detailed", "further", "more", "recap", "continued", "continuation", "example", "examples",
}


def _trim(s: str) -> str:
    """Strip separators at the edges but keep a balanced closing ')': 'Memory Management (Stack)'
    stays whole (a plain strip of '()' turned it into 'Memory Management (Stack', live job 3)."""
    s = s.strip(" .,:/+-")
    while s and (s[0] in "()" or s[-1] == "(" or (s[-1] == ")" and s.count(")") > s.count("("))):
        s = (s[1:] if s[0] in "()" else s[:-1]).strip(" .,:/+-")
    return s


def _balance(s: str) -> str:
    """Drop a parenthetical left open by truncation: 'Memory Management (Stack and He' -> 'Memory Management'."""
    while s.count("(") > s.count(")"):
        s = _trim(s[:s.rfind("(")])
    return s


def _topic_variants(value: object) -> list[str]:
    """Use only words actually present in a MAP topic; never manufacture subjects."""
    if not isinstance(value, str):
        return []
    text = _plain(value).replace("'s", "").replace("'", "").replace("&", "and")
    text = re.sub(r"[^A-Za-z0-9_$ .,:()/+-]", " ", text)
    text = _trim(re.sub(r"\s+", " ", text))
    words = list(re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*", text))
    variants = []
    for word in words:
        if word.group().casefold() in _GENERIC_HEADS:
            continue
        variant = _trim(text[word.start():])
        if len(variant) > 48:
            variant = _trim(variant[:49].rsplit(" ", 1)[0])
        variant = _balance(re.sub(r"\s+(?:and|or|of|to|in|with|vs)\.?$", "", variant, flags=re.IGNORECASE))
        if variants:
            prefix = _balance(_trim(text[:word.start()]))
            prefix = re.sub(r"\s+(?:and|or|of|to|in|with|vs)\.?$", "", prefix, flags=re.IGNORECASE)
            room = 48 - len(variant) - 3
            if len(prefix) > room:
                prefix = prefix[:max(0, room) + 1].rsplit(" ", 1)[0] if room > 0 else ""
            if prefix and len(prefix) <= room:
                variant += f" ({prefix})"
        if variant and variant not in variants:
            variants.append(variant)
    return variants


def _chapter_plan(summaries: list[dict], keep) -> list[dict]:
    """Select grounded transitions spread over kept time, with distinct topic heads."""
    total = timeline.duration(keep)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid kept duration")
    minimum, maximum = _chapter_limits(total)
    candidates = []
    for part in summaries:
        changes = part.get("topic_changes", [])
        if not isinstance(changes, list):
            changes = []
        entries = [{"t": part.get("start"), "topic": part.get("topic")}, *changes]
        for change in entries:
            if not isinstance(change, dict) or isinstance(change.get("t"), bool):
                continue
            try:
                original = float(change["t"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(original) or not any(a <= original <= b for a, b in keep):
                continue
            mapped = timeline.to_final(original, keep)
            variants = _topic_variants(change.get("topic"))
            if variants and mapped <= total - 30:
                candidates.append({"t": original, "final_t": mapped, "variants": variants,
                                   "source_topic": change["topic"], "context": part.get("summary", "")})
    candidates.sort(key=lambda c: (c["final_t"], c["source_topic"]))
    if not candidates:
        raise ValueError("no grounded chapter topics in kept content")

    # Retry smaller plans if the source supplies fewer distinct, well-spaced subjects.
    target = min(maximum, (minimum + maximum) // 2)
    for count in range(target, HARD_MIN_CHAPTERS - 1, -1):
        chosen, heads = [], set()
        for index in range(count):
            wanted = total * index / count
            earliest = 0 if not chosen else chosen[-1]["final_t"] + 60
            if index == count - 1:
                earliest = max(earliest, total * 0.8)
            latest = total - 30 - (count - index - 1) * 60
            options = []
            for candidate in candidates:
                mapped = 0.0 if index == 0 else candidate["final_t"]
                if index == 0 and candidate["final_t"] > min(120, total * 0.1):
                    continue
                if not earliest <= mapped <= latest:
                    continue
                for rank, topic in enumerate(candidate["variants"]):
                    head = re.search(r"[A-Za-z_$][A-Za-z0-9_$]*", topic).group().casefold()
                    if head in heads:
                        continue
                    score = abs(candidate["final_t"] - wanted) + rank * total / count / 3
                    options.append((score, candidate["final_t"], rank, topic, head, candidate))
            if not options:
                break
            _, _, _, topic, head, candidate = min(options, key=lambda option: option[:4])
            heads.add(head)
            chosen.append({"id": f"c{index}", "t": keep[0][0] if index == 0 else candidate["t"],
                           "final_t": 0.0 if index == 0 else candidate["final_t"], "topic": topic,
                           "source_topic": candidate["source_topic"], "context": candidate["context"]})
        if len(chosen) == count:
            return chosen
    raise ValueError(f"need at least {HARD_MIN_CHAPTERS} distinct grounded chapter topics across the kept lesson")


def _parts(segments: list[dict], keep) -> list[dict]:
    kept = [s for s in segments
            if any(a <= (s["start"] + s["end"]) / 2 <= b for a, b in keep) and s["text"]]
    parts, cur = [], []
    for s in kept:
        if cur and s["start"] - cur[0]["start"] >= PART_SEC:
            parts.append(cur)
            cur = []
        cur.append(s)
    if cur:
        parts.append(cur)
    return parts


def _armenian_topic(topic: str, *, definite: bool = False) -> str:
    caption = re.sub(r"(?<!\w)(?:and|vs\.?|versus)(?!\w)", "և", topic, flags=re.IGNORECASE)
    if definite and not _ARMENIAN_TEXT.search(caption):
        subject, separator, context = caption.partition(" (")
        caption = subject + "-ը" + (separator + context if separator else "")
    return caption


def _grounded_fallback(plan: list[dict], keep) -> dict:
    """Publish only the already-grounded subjects when model wording is unusable."""
    subjects = [_armenian_topic(c["topic"]) for c in plan]
    title = "Դաս՝ " + subjects[0]
    for subject in subjects[1:3]:            # the lesson's first main subjects, within 100 chars
        if subject not in title and len(title + ", " + subject) <= 100:
            title += ", " + subject
    middle = (len(subjects) + 1) // 2
    description = (
        f"Դասը նվիրված է {', '.join(subjects[:middle])} թեմաներին։ "
        f"Նյութը ներառում է նաև {', '.join(subjects[middle:])} թեմաները։ "
        "Ժամանակային նշումներով կարելի է անցնել համապատասխան թեմայի բաժնին։"
    )
    return _validate({"title": title, "description": description,
                      "chapters": [{"t": c["t"], "title": _armenian_topic(c["topic"], definite=True)}
                                   for c in plan]}, keep, require_armenian=True)



def _inputs(transcript_json: str | Path, keep) -> list[dict]:
    """Broken source data and cut intervals remain critical; model failures do not."""
    if not isinstance(keep, (list, tuple)) or not keep:
        raise ValueError("invalid keep intervals")
    previous = 0.0
    for interval in keep:
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in interval)):
            raise ValueError("invalid keep intervals")
        start, end = interval
        if start < previous or end <= start:
            raise ValueError("invalid keep intervals")
        previous = end
    transcript = json.loads(Path(transcript_json).read_text())
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("invalid transcript segments")
    for segment in transcript["segments"]:
        if (not isinstance(segment, dict) or not isinstance(segment.get("text"), str)
                or any(type(segment.get(key)) not in (int, float) or not math.isfinite(segment[key])
                       for key in ("start", "end"))
                or segment["start"] < 0 or segment["end"] < segment["start"]):
            raise ValueError("invalid transcript segment")
    return transcript["segments"]


def _safe_lesson_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[<>\x00-\x1f\x7f]", " ", value)).strip()


def _default_context(out_dir: Path) -> tuple[str, str]:
    """Read only a matching job; standalone callers need no database or context."""
    local_zone = ZoneInfo("Asia/Yerevan")
    lesson, day = "Դաս", datetime.now(timezone.utc).astimezone(local_zone).date().isoformat()
    directory = Path(out_dir).absolute()
    if directory.parent.name != "work" or not directory.name.isascii() or not directory.name.isdigit():
        return lesson, day
    database = directory.parent.parent / "reel.db"
    if not database.is_file() or database.is_symlink():
        return lesson, day
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
        row = connection.execute("SELECT source_path, grp, created_at FROM jobs WHERE id=?",
                                 (int(directory.name),)).fetchone()
        if row is None:
            return lesson, day
        source, group, created_at = row
        name = Path(source).name if isinstance(source, str) else ""
        if Path(name).suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"}:
            name = Path(name).stem
        lesson = _safe_lesson_name(name) or _safe_lesson_name(group) or lesson
        if isinstance(created_at, str):
            try:
                created = datetime.fromisoformat(created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                day = created.astimezone(local_zone).date().isoformat()
            except ValueError:
                pass
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        pass  # Optional context must never turn a metadata default into a failed lesson.
    finally:
        if connection is not None:
            connection.close()
    return lesson, day


def _default_metadata(note: str, summaries, plan, out_dir: Path, extra_footer: str, *, language="hy") -> dict:
    lesson, day = _default_context(out_dir)
    prefix, suffix = "Դաս՝ ", f", {day}"
    if language == "en":
        prefix = "Lesson: "
        if lesson == "Դաս":
            lesson = "Recording"
    title = prefix + lesson[:100 - len(prefix) - len(suffix)].rstrip() + suffix
    meta = {"title": title,
            "description": "Դասի տեսագրություն։ Ավտոմատ նկարագրությունն ու ժամանակային բաժինները հասանելի չեն։",
            "chapters": [], "auto_note": note}
    if language == "en":
        meta["description"] = "Lesson recording. An automatic description and chapters are unavailable."
    return _finish(meta, "default", summaries, plan, Path(out_dir), extra_footer)


def generate(transcript_json: str | Path, keep, out_dir: Path, *,
             model: str = llm.DEFAULT_MODEL, extra_footer: str = "", language: str = "hy") -> dict:
    if language not in ("hy", "en"):
        raise ValueError("Unsupported metadata language")
    segs = _inputs(transcript_json, keep)
    summaries = []
    for p in _parts(segs, keep):
        text = "\n".join(f"[{s['start']:.0f}] {s['text']}" for s in p)
        try:
            r = llm.chat_json(MAP_SYS, text, model=model)
            if (not isinstance(r, dict) or not isinstance(r.get("topic"), str)
                    or not isinstance(r.get("summary"), str) or not isinstance(r.get("topic_changes"), list)
                    or any(not isinstance(change, dict) for change in r["topic_changes"])):
                raise ValueError("invalid metadata map response")
        except (llm.LLMError, ValueError, KeyError, TypeError):
            return _default_metadata("LLM metadata unavailable — default metadata", summaries, [], out_dir, extra_footer, language=language)
        summaries.append({**r, "start": p[0]["start"], "end": p[-1]["end"]})

    try:
        plan = _chapter_plan(summaries, keep)
    except ValueError:
        return _default_metadata("grounded chapters unavailable — default metadata", summaries, [], out_dir, extra_footer, language=language)
    if language == "en":
        topics = [entry["topic"] for entry in plan]
        title = ", ".join(dict.fromkeys(topics[:3]))
        while len(title) > 100 and ", " in title:
            title = title.rsplit(", ", 1)[0]
        meta = _validate({"title": title, "description": "Topics covered: " + "; ".join(dict.fromkeys(topics)) + ".",
                          "chapters": [{"t": entry["t"], "title": entry["topic"]} for entry in plan]}, keep)
        return _finish(meta, "grounded", summaries, plan, out_dir, extra_footer)
    if not LLM_TEXT:
        return _finish(_grounded_fallback(plan, keep), "grounded", summaries, plan, out_dir, extra_footer)
    reduce_in = json.dumps({"duration_sec": timeline.duration(keep),
                            "chapter_plan": [{**c, "detail_max_chars": _detail_limit(c["topic"])} for c in plan],
                            "keep_intervals": keep,
                            "parts": summaries}, ensure_ascii=False)
    meta = None
    valid_reducer_response = False
    last_text = None          # model title/description from a structurally valid response
    generation_mode = "llm"
    feedback = ""
    for attempt in range(2):
        try:
            response = llm.chat_json(REDUCE_SYS + feedback, reduce_in, model=model, temperature=0.3,
                                     schema=_reduce_schema(plan))
            if (not isinstance(response, dict) or not isinstance(response.get("title"), str)
                    or not isinstance(response.get("description"), str)):
                raise ValueError("reducer must contain string title and description fields")
            details = response.get("chapter_details")
            if (not isinstance(details, dict) or set(details) != {c["id"] for c in plan}
                    or any(not isinstance(detail, str) for detail in details.values())):
                raise ValueError("chapter_details must match every fixed chapter ID exactly")
            valid_reducer_response = True
            last_text = (response["title"], response["description"])
            meta = _validate({"title": response["title"], "description": response["description"],
                              "chapters": [{"t": c["t"], "topic": c["topic"], "title": details[c["id"]]}
                                           for c in plan]}, keep, require_armenian=True)
            break
        except (llm.LLMError, ValueError, KeyError, TypeError) as exc:
            feedback = f"\nPrevious response failed validation: {exc}. Correct that error and return complete JSON again."
            continue
    if meta is None:
        if not valid_reducer_response:
            return _default_metadata("LLM metadata unavailable — default metadata", summaries, plan, out_dir, extra_footer, language=language)
        meta = _grounded_fallback(plan, keep)
        generation_mode = "grounded_fallback"
        if last_text is not None:   # grounded chapters + the model's natural title/description
            try:
                meta = _validate({"title": last_text[0], "description": last_text[1],
                                  "chapters": [{"t": c["t"], "title": c["title"]} for c in meta["chapters"]]},
                                 keep, require_armenian=True)
                generation_mode = "llm_text_grounded_chapters"
            except (ValueError, KeyError, TypeError):
                pass
        meta["auto_note"] = "LLM chapter wording unusable — grounded chapters kept"

    return _finish(meta, generation_mode, summaries, plan, out_dir, extra_footer)


def _finish(meta: dict, generation_mode: str, summaries, plan, out_dir: Path, extra_footer: str) -> dict:
    lines = [f"{fmt(c['final_t'])} {c['title']}" for c in meta["chapters"]]
    description = meta["description"].strip() + "\n\n" + "\n".join(lines)
    if extra_footer:
        description += "\n\n" + extra_footer
    meta["youtube_description"] = description[:5000]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps({**meta, "parts": summaries, "chapter_plan": plan,
                                                     "generation_mode": generation_mode},
                                                     ensure_ascii=False, indent=1))
    return meta


_TERMS = {
    "ֆունկցիա": "function", "ֆունքցիա": "function",
    "հոյստինգ": "hoisting", "հոիստինգ": "hoisting",
    "լեքսիկալ": "lexical", "լեկսիկալ": "lexical",
    "լեքսիկական": "lexical", "լեկսիկական": "lexical",
    "ասինքրոն": "asynchronous", "ասինխրոն": "asynchronous",
    "սքոուպ": "scope", "սկոուպ": "scope", "սկոպ": "scope", "սքոփ": "scope",
    "ինտերպրետատոր": "interpreter", "բայթկոդ": "bytecode", "բայթքոդ": "bytecode",
    "կոմպիլյատոր": "compiler", "կոմպայլեր": "compiler",
    "քլոժեր": "closure", "կլոժեր": "closure", "քլոուժեր": "closure",
    "դեկլարացիա": "declaration", "էքսպրեշն": "expression",
    "կոնտեքստ": "context", "պրոտոտիպ": "prototype",
    "արգումենտ": "argument", "օբյեկտ": "object",
    "դիս": "this", "թիս": "this", "բայնդ": "bind",
}
_ARMENIAN_LETTERS = r"\u0531-\u0556\u0561-\u0587"
_ARMENIAN_TEXT = re.compile("[" + _ARMENIAN_LETTERS + "]")
_THIS_SUFFIXES = {"", "ը", "ն", "ի", "ին", "ից", "ով", "ում"}
_TERM_PATTERN = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(term) for term in sorted(_TERMS, key=len, reverse=True))
    + r")([" + _ARMENIAN_LETTERS + r"]*)(?![\w])", re.IGNORECASE)


def _normalize_terms(text: str) -> str:
    """Replace known phonetic spellings, preserving Armenian grammatical endings."""
    def replace(match):
        term, suffix = match.group(1).casefold(), match.group(2).casefold()
        if term in {"դիս", "թիս"} and suffix not in _THIS_SUFFIXES:
            return match.group(0)
        if term.endswith("իա"):
            if suffix.startswith("յ"):
                suffix = suffix[1:]  # Drop the phonetic linking consonant before a Latin term.
            elif suffix == "ն":
                suffix = "ը"
        return _TERMS[term] + ("-" + suffix if suffix else "")
    return _TERM_PATTERN.sub(replace, text)


def _plain(text) -> str:
    return _normalize_terms(str(text).replace("`", "").replace("*", "").strip())


def _check_plain(text: str) -> None:
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-+]\s|>\s)", text) or re.search(r"\[[^]]+\]\([^)]+\)", text):
        raise ValueError("metadata must be plain text without markdown")


def _validate(m: dict, keep, require_armenian: bool = False) -> dict:
    if not isinstance(m, dict) or not isinstance(m.get("chapters"), list) or any(not isinstance(c, dict) for c in m["chapters"]):
        raise ValueError("metadata must contain a chapter list of objects")
    if not isinstance(m["title"], str) or not isinstance(m["description"], str):
        raise ValueError("title/description must be text")
    title = _plain(m["title"]).replace("<", "").replace(">", "")
    description = _plain(m["description"])
    if not title or not description:
        raise ValueError("empty title/description")
    if len(title) > 100:
        raise ValueError("lesson title exceeds 100 characters; use a shorter complete title")
    if re.search(r"(?<!\w)(?:աշխարհը|գաղտնիքներ(?:ը|ի)?|հետադիտում(?:ը|ի)?)(?!\w)", title, flags=re.IGNORECASE):
        raise ValueError("lesson title must name specific technical subjects without clickbait or retrospective clichés")
    if require_armenian and any(not _ARMENIAN_TEXT.search(text) for text in (title, description)):
        raise ValueError("title and description must be Armenian")
    for text in (title, description):
        _check_plain(text)
    total = timeline.duration(keep)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("invalid kept duration")
    chapters, last, first_words = [], None, set()
    for c in sorted(m["chapters"], key=lambda c: float(c["t"])):
        original = float(c["t"])
        if not math.isfinite(original) or not any(a <= original <= b for a, b in keep):
            raise ValueError("chapter timestamp must belong to kept content")
        if not isinstance(c["title"], str):
            raise ValueError("chapter title must be text")
        chapter_title = _plain(c["title"])
        _check_plain(chapter_title)
        if "topic" in c:
            if not isinstance(c["topic"], str):
                raise ValueError("chapter topic must be ASCII English text")
            topic = _plain(c["topic"])
            if len(topic) > 48 or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$ .,:()/+-]*", topic):
                raise ValueError("chapter topic must be ASCII English text")
            if (len(chapter_title) > _detail_limit(topic) or not 2 <= len(chapter_title.split()) <= 4
                    or re.search(r"[\n\r.!?։՜՞]", chapter_title)
                    or re.search(r"(?<!\w)(?:է|են|եմ|ես|ենք|եք|կարող|պետք|լինում|լինել)(?!\w)", chapter_title)):
                raise ValueError(f"chapter detail must be a complete 2-4-word noun phrase within {_detail_limit(topic)} characters")
            if not re.match(r"^[^\w]*" + re.escape(topic) + r"(?=$|[^\w])", chapter_title, flags=re.IGNORECASE):
                chapter_title = f"{topic}․ {chapter_title}"
        if len(chapter_title) > 80:
            raise ValueError("chapter title exceeds 80 characters; use a shorter complete title")
        if not chapter_title or (require_armenian and not _ARMENIAN_TEXT.search(chapter_title)):
            raise ValueError("every chapter needs a non-empty Armenian title")
        _check_plain(chapter_title)
        ft = timeline.to_final(original, keep)
        if last is not None and ft - last < 60:
            continue
        if ft > total - 30:
            continue
        word = re.search(r"[^\W_]+", chapter_title, flags=re.UNICODE)
        if not word:
            raise ValueError("chapter needs a concrete topic")
        first_word = word.group().casefold()
        if first_word in first_words:
            raise ValueError(f"repeated first chapter word: {first_word}; use distinct specific topics")
        first_words.add(first_word)
        chapters.append({"t": original, "final_t": ft, "title": chapter_title})
        last = ft
    _, maximum = _chapter_limits(total)
    if not HARD_MIN_CHAPTERS <= len(chapters) <= maximum:
        raise ValueError(f"need {HARD_MIN_CHAPTERS}-{maximum} chapters for {total/60:.0f} minutes, got {len(chapters)}")
    chapters[0]["final_t"] = 0.0
    return {"title": title, "description": description, "chapters": chapters}
