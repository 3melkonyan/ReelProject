"""Load and validate the public Reel configuration."""
import copy
import os
from pathlib import Path
import math
import re
import shutil
import tempfile
import yaml


def _mapping(value: object, name: str, keys: set[str], optional: frozenset = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    if not keys <= set(value) <= keys | optional:
        raise ValueError(f"{name}: missing or unknown fields; expected {sorted(keys)}"
                         + (f", optional {sorted(optional)}" if optional else ""))
    return value


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


GROUP_NAME = re.compile(r"^[^\W_][\w .-]{0,59}$")    # also the inbox/<group>/ folder name
MAX_SUBJECTS = 5


def _resolve(path) -> Path:
    target = Path(path)       # relative = project root, resolved at call time (smoke patches __file__)
    return target if target.is_absolute() else Path(__file__).resolve().parents[1] / target


def load_config(path: str = "config.yaml") -> dict:
    try:
        with _resolve(path).open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("Cannot read configuration YAML") from exc
    return validate_config(config)


def validate_config(config: object) -> dict:
    """Validate and NORMALISE a configuration (deep copy). Contract for every reader/writer:
    groups.<name> = {"subjects": [{"name": str, "playlist": id, "" = none yet}] (1..5, the courses the group studies
    now, each with its own YouTube playlist), "slack_channel": str (the group's one chat),
    "lecturers": [names] (may be empty = automatic lecturer), "classroom_course_id": str (optional)}.
    Legacy 'playlist_id: X' is accepted and becomes one subject named after the group."""
    config = copy.deepcopy(config)
    _mapping(config, "config", {"groups", "operator_slack_channel", "cut"}, frozenset({"time_savings", "ui_language"}))
    if "ui_language" in config:
        from reel.i18n import validate_language
        validate_language(config["ui_language"])
    if "time_savings" in config:
        assumptions = _mapping(config["time_savings"], "time_savings",
                               {"editing_min_per_hour", "publishing_min_per_lesson"})
        for key, maximum in (("editing_min_per_hour", 240), ("publishing_min_per_lesson", 120)):
            value = assumptions[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"time_savings.{key} must be between 0 and {maximum}")
    _text(config["operator_slack_channel"], "operator_slack_channel")
    groups = config["groups"]
    if not isinstance(groups, dict) or not groups:
        raise ValueError("groups must be a non-empty mapping")
    for name, group in groups.items():
        _text(name, "group name")
        if not GROUP_NAME.match(name):
            raise ValueError(f"group name '{name}': letters, digits, space, '-', '.', '_' only (max 60)")
        _mapping(group, f"groups.{name}", {"slack_channel"},
                 optional=frozenset({"playlist_id", "subjects", "lecturers", "classroom_course_id", "framing", "framing_correction", "processing"}))
        if "processing" in group:
            from reel.stages.processing import settings
            settings(group["processing"])
        if ("playlist_id" in group) == ("subjects" in group):
            raise ValueError(f"groups.{name}: give 'subjects' (list) or the legacy 'playlist_id', exactly one")
        if "playlist_id" in group:
            subjects = [{"name": name, "playlist": group.pop("playlist_id")}]
        else:
            subjects = group["subjects"]
        if not isinstance(subjects, list) or not 1 <= len(subjects) <= MAX_SUBJECTS:
            raise ValueError(f"groups.{name}.subjects must list 1-{MAX_SUBJECTS} subjects")
        clean, names, playlists = [], set(), set()
        for item in subjects:
            if not isinstance(item, dict) or "name" not in item or set(item) - {"name", "playlist"}:
                raise ValueError(f"groups.{name}.subjects: every subject needs 'name' and may have 'playlist'")
            _text(item["name"], f"groups.{name}.subjects.name")
            pl = item.get("playlist")
            if pl is None:
                pl = ""     # no playlist yet -> the lesson is published without one
            if not isinstance(pl, str):
                raise ValueError(f"groups.{name}.subjects.playlist must be a string")
            subject, pl = item["name"].strip(), pl.strip()
            if len(subject) > 40 or subject.casefold() in names or (pl and pl in playlists):
                raise ValueError(f"groups.{name}.subjects: names (<=40 chars) and playlists must be unique")
            names.add(subject.casefold())
            if pl:
                playlists.add(pl)
            clean.append({"name": subject, "playlist": pl})
        group["subjects"] = clean
        _text(group["slack_channel"], f"groups.{name}.slack_channel")
        if group.get("classroom_course_id") in (None, ""):      # absent or empty = Classroom off
            group.pop("classroom_course_id", None)
        else:
            _text(group["classroom_course_id"], f"groups.{name}.classroom_course_id")
        lecturers = group.get("lecturers") or []                 # empty = automatic lecturer detection
        if not isinstance(lecturers, list):
            raise ValueError(f"groups.{name}.lecturers must be a list")
        for lecturer in lecturers:
            _text(lecturer, "lecturer")
        group["lecturers"] = lecturers
        if "framing" in group and group["framing"] not in ("auto", "board", "projector", "off"):
            raise ValueError(f"groups.{name}.framing must be 'auto', 'board', 'projector' or 'off'")
        if "framing_correction" in group and group["framing_correction"] not in ("rotate", "perspective"):
            raise ValueError(f"groups.{name}.framing_correction must be 'rotate' or 'perspective'")
    cut = config["cut"]
    if not isinstance(cut, dict) or not {"min_silence_sec", "max_cut_ratio"} <= cut.keys():
        raise ValueError("cut requires min_silence_sec and max_cut_ratio")
    for field in cut:
        _text(field, "cut key")
        value = cut[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"cut.{field} must be a finite number")
    if cut["min_silence_sec"] <= 0 or not 0 < cut["max_cut_ratio"] < 1:
        raise ValueError("cut requires min_silence_sec > 0 and 0 < max_cut_ratio < 1")
    return config



def save_config(config: dict, path: str = "config.yaml") -> dict:
    """Validate, then atomically replace config.yaml; the previous file is kept as config.yaml.bak.
    Writers (the admin settings page) must use this; the worker reloads the file on mtime change."""
    normalized = validate_config(config)
    target = _resolve(path)
    text = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(tmp, 0o644)
        if target.exists():
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return normalized


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()
    validated = load_config(args.config)
    from tempfile import TemporaryDirectory
    from unittest.mock import patch
    from copy import deepcopy
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        target = root / "fixture.yaml"
        fixture = deepcopy(validated)
        fixture["cut"]["pad_sec"] = 0.25
        target.write_text(yaml.safe_dump(fixture), encoding="utf-8")
        with patch.dict(load_config.__globals__, {"__file__": str(root / "reel" / "config.py")}):
            assert load_config("fixture.yaml")["cut"]["pad_sec"] == 0.25
        for invalid in ("bad", True, float("inf"), float("nan")):
            fixture["cut"]["pad_sec"] = invalid
            target.write_text(yaml.safe_dump(fixture), encoding="utf-8")
            try:
                load_config(str(target))
                raise AssertionError("Expected numeric validation error")
            except ValueError:
                pass
        fixture["cut"].pop("min_silence_sec")
        target.write_text(yaml.safe_dump(fixture), encoding="utf-8")
        try:
            load_config(str(target))
            raise AssertionError("Expected mandatory cut key error")
        except ValueError:
            pass
    print("OK: configuration validated (offline)")
