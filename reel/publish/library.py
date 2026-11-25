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
        import fcntl, os
        path = _path()
        if path.is_symlink(): raise OSError('Invalid library path')
        fd = os.open(str(path)+'.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with path.open("a", encoding="utf-8") as stream:
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


def remove_job(path: Path, job_id: int) -> None:
    """Atomically retire sidecar entries while other lessons can still be published."""
    import fcntl
    import os
    import tempfile
    path = Path(path)
    if path.is_symlink(): raise PublishError('Library path is a symlink', False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path)+'.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not path.exists(): return
        kept=[]
        for line in path.read_text().splitlines(keepends=True):
            try: match=str(json.loads(line).get('job_id'))==str(job_id)
            except (ValueError,AttributeError): match=False
            if not match: kept.append(line)
        out,temporary=tempfile.mkstemp(prefix='.library-',dir=path.parent)
        try:
            with os.fdopen(out,'w') as stream:
                stream.writelines(kept);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally: Path(temporary).unlink(missing_ok=True)
