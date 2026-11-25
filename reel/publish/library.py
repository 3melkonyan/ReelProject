"""Append publication records to the project library; no queue or deduplication."""
import json
from pathlib import Path
from reel.publish.errors import PublishError
from reel.publish._common import text, link


def _path() -> Path:
    return Path(__file__).resolve().parents[2] / "library.jsonl"


def add(entry: dict) -> None:
    if not isinstance(entry, dict):
        raise PublishError("Library entry must be a mapping", False)
    for field in ("job_id", "group", "title", "url", "published_at"):
        text(entry.get(field), field)
    link(entry["url"])
    if entry.get("subject") is not None:
        text(entry["subject"], "subject")
    if entry.get("lesson_no") is not None and (type(entry["lesson_no"]) is not int or entry["lesson_no"] < 1):
        raise PublishError("lesson_no must be a positive integer", False)
    if entry.get("duration") is not None:
        import math
        value = entry["duration"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise PublishError("duration must be finite and non-negative", False)
    if "chapters" in entry and (not isinstance(entry["chapters"], list) or
                               any(not isinstance(c, dict) for c in entry["chapters"])):
        raise PublishError("chapters must be a list of mappings", False)
    try:
        line = json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n"
        with _path().open("a", encoding="utf-8") as stream:
            stream.write(line)
    except (OSError, TypeError, ValueError) as exc:
        raise PublishError(f"Cannot append library record ({type(exc).__name__})", False) from None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    from reel.publish._smoke import library_smoke
    library_smoke()
