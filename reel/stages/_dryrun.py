"""Fake publishers for REEL_DRY_RUN=1: same signatures as reel.publish.*, no network.

Every call is appended to out/dryrun_calls.jsonl. Fake YouTube keeps a local
registry so find_by_tag() behaves like the real one (idempotency tests work).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "out" / "dryrun_calls.jsonl"
REG = ROOT / "out" / "dryrun_youtube.json"


def _log(target: str, fn: str, **kw) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"t": time.time(), "target": target, "fn": fn, **kw},
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


def _reg() -> dict:
    return json.loads(REG.read_text()) if REG.exists() else {}


def _upload(video_path, title, description, tags, privacy="unlisted"):
    assert Path(video_path).is_file(), video_path
    assert len(title) <= 100 and len(description) <= 5000
    reg = _reg()
    vid = f"dry{len(reg) + 1:04d}"
    reg[vid] = {"tags": tags, "title": title}
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    _log(
        "youtube",
        "upload",
        video=str(video_path),
        title=title,
        privacy=privacy,
        tags=tags,
        description=description,
    )
    return {"video_id": vid, "url": f"https://youtu.be/{vid}"}


def _find_by_tag(tag):
    return next((v for v, d in _reg().items() if tag in d["tags"]), None)


youtube = SimpleNamespace(
    upload=_upload,
    delete_video=lambda vid: _log("youtube", "delete_video", video_id=vid),
    find_by_tag=_find_by_tag,
    add_to_playlist=lambda vid, pl: (
        _log("youtube", "add_to_playlist", video_id=vid, playlist=pl),
        f"pli-{vid}",
    )[1],
    is_in_playlist=lambda vid, pl: False,
    processing_status=lambda vid: "processed",
    set_thumbnail=lambda vid, path: _log("youtube", "set_thumbnail", video_id=vid, path=path),
    find_captions=lambda vid, language="hy": None,
    upload_captions=lambda vid, path, language="hy": (
        _log("youtube", "upload_captions", video_id=vid, path=path, language=language),
        f"cap-{vid}",
    )[1],
)
slack = SimpleNamespace(
    delete_message=lambda channel, timestamp: _log(
        "slack", "delete_message", channel=channel, timestamp=timestamp
    ),
    post_link=lambda ch, title, url: (
        _log("slack", "post_link", channel=ch, title=title, url=url),
        "dry-ts",
    )[1],
    notify=lambda text: (
        _log("slack", "notify", text=text),
        {"channel": "dry-operator", "ts": str(time.time_ns())},
    )[1],
)
classroom = SimpleNamespace(
    announce=lambda course, title, url: (
        _log("classroom", "announce", course=course, title=title, url=url),
        "dry-ann",
    )[1],
)
library = SimpleNamespace(add=lambda entry: _log("library", "add", **entry))
