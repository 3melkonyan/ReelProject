"""Render summaries, using source-exec under a temporary app root only."""
from __future__ import annotations

import builtins
import copy
import io
import os
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4

import pytest

from reel.db import DB

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def web(tmp_path, monkeypatch):
    source = PROJECT / "reel" / "web" / "app.py"
    fake_file = tmp_path / "reel" / "web" / "app.py"
    (fake_file.parent / "static").mkdir(parents=True)
    protected = [PROJECT / name for name in ("secrets", "work", "reel.db", "config.yaml")]

    def protect(original):
        def checked(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                target = Path(os.path.abspath(os.fsdecode(path)))
                assert not any(target == base or target.is_relative_to(base) for base in protected), "production data access"
            return original(path, *args, **kwargs)
        return checked

    monkeypatch.setattr(builtins, "open", protect(builtins.open))
    monkeypatch.setattr(io, "open", protect(io.open))

    def no_database(*args, **kwargs):
        raise AssertionError("summary checks must not open any database")

    monkeypatch.setattr(DB, "__init__", no_database)
    module = ModuleType(f"_reel_g25_summary_{uuid4().hex}")
    module.__file__ = str(fake_file)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(source.read_text(encoding="utf-8"), str(fake_file), "exec"), module.__dict__)
    assert module.ROOT == tmp_path
    assert module.WORK == tmp_path / "work"
    assert module.SECRETS_FILE == tmp_path / "secrets" / "web.json"
    return module


def test_framed_render_exposes_only_board_and_framing_measurements(web):
    output = {
        "lufs": -16.2, "board": "framed", "board_tilt_deg": -1.75, "board_agree": 0.9,
        "path": "frame", "final": "/private/final.mp4", "source": {"private": "raw metadata"},
        "detector": {"corners": [[1, 2]], "raw": "private detector blob"},
        "framing": {"size": [3840, 2160], "rotate_deg": 1.75, "crop": [200, 100, 3200, 1800],
                    "zoom": 1.2, "board_cut": 0.02, "corners": [[1, 2]], "debug": "private framing blob"},
    }
    before = copy.deepcopy(output)
    assert web._stage_summary("render", output) == {
        "lufs": -16.2, "board": "framed", "board_tilt_deg": -1.75, "board_agree": 0.9,
        "framing": {"size": [3840, 2160], "rotate_deg": 1.75, "crop": [200, 100, 3200, 1800],
                    "zoom": 1.2, "board_cut": 0.02},
    }
    assert output == before


@pytest.mark.parametrize("reason", ["portrait video", "board not found reliably (3/20 frames)",
                                  "framing would cut 25% of the board", "board detection failed (ValueError)"])
def test_board_fallback_reason_is_preserved_without_raw_output(web, reason):
    board = reason + " — full frame kept"
    assert web._stage_summary("render", {"lufs": -16, "board": board, "final": "private.mp4"}) == {
        "lufs": -16, "board": board,
    }


def test_no_board_keeps_the_previous_summary_shape(web):
    assert web._stage_summary("render", {
        "lufs": -15.8, "board_tilt_deg": 2, "board_agree": 1,
        "framing": {"zoom": 1.2, "rotate_deg": 2}, "path": "frame", "source": {"raw": "private"},
    }) == {"lufs": -15.8}
    assert web._stage_summary("render", {}) == {}
    assert web._stage_summary("render", {"board": "framed"}) == {"board": "framed"}


@pytest.mark.parametrize("framing", [None, [], "raw blob", {"debug": {"raw": "private"}}])
def test_nonmeasurement_framing_is_not_forwarded(web, framing):
    assert web._stage_summary("render", {"board": "framed", "framing": framing}) == {"board": "framed"}


def test_wrong_types_nonfinite_values_and_nested_blobs_are_not_forwarded(web):
    result = web._stage_summary("render", {
        "lufs": -16, "board": "framed", "board_tilt_deg": float("nan"), "board_agree": {"private": "blob"},
        "framing": {"rotate_deg": True, "zoom": float("inf"), "board_cut": {"private": "blob"},
                    "size": [3840, {"private": "blob"}], "crop": [0, 0, 1920, float("nan")]},
    })
    assert result == {"lufs": -16, "board": "framed"}
    assert web._stage_summary("render", {"lufs": -16, "board": {"raw": "blob"},
                                         "framing": {"zoom": 2}}) == {"lufs": -16}


@pytest.mark.parametrize("stage, output, expected", [
    ("preview", {"size_mb": 12, "skipped": "not needed", "board": "framed"}, {"size_mb": 12, "skipped": "not needed"}),
    ("blur", {"lecturer_mode": "auto", "board": "framed", "framing": {"zoom": 2}}, {"lecturer_mode": "auto"}),
    ("unknown", {"board": "framed", "lufs": -16}, {}),
])
def test_other_stage_summaries_are_unchanged(web, stage, output, expected):
    assert web._stage_summary(stage, output) == expected
