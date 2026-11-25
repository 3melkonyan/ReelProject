"""Config contract: a group studies 1-5 subjects, each with its own playlist; safe atomic save."""

import pytest
import yaml

from reel.config import load_config, save_config, validate_config


def cfg(**group):
    return {
        "operator_slack_channel": "C1",
        "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
        "groups": {"Group-A": {"slack_channel": "C1", **group}},
    }


PY = {"name": "Python", "playlist": "PL1"}
JS = {"name": "JavaScript", "playlist": "PL2"}


def test_legacy_playlist_id_becomes_one_subject_and_lecturers_default_to_auto():
    g = validate_config(cfg(playlist_id="PL1"))["groups"]["Group-A"]
    assert g == {
        "slack_channel": "C1",
        "subjects": [{"name": "Group-A", "playlist": "PL1"}],
        "lecturers": [],
    }


def test_subjects_ok_and_bad_lists_rejected():
    assert validate_config(cfg(subjects=[PY, JS]))["groups"]["Group-A"]["subjects"] == [PY, JS]
    for bad in (
        {"subjects": []},
        {"subjects": [PY, {"name": "python", "playlist": "PL9"}]},  # same name
        {"subjects": [PY, {"name": "Go", "playlist": "PL1"}]},  # same playlist
        {"subjects": [{"name": f"S{i}", "playlist": f"P{i}"} for i in range(6)]},
        {
            "subjects": [{"name": "x" * 41, "playlist": "P"}]
        },  # a missing playlist is OK since 2026-11-19
        {"subjects": [PY], "playlist_id": "PL2"},
        {},
        {"subjects": "Python"},
    ):
        with pytest.raises(ValueError):
            validate_config(cfg(**bad))


def test_group_name_is_a_safe_folder_name():
    for bad in ("../x", ".hidden", "a/b", ""):
        c = cfg(subjects=[PY])
        c["groups"] = {bad: c["groups"]["Group-A"]}
        with pytest.raises(ValueError):
            validate_config(c)
    c = cfg(subjects=[PY])
    c["groups"] = {"Խումբ 3": c["groups"]["Group-A"]}  # Armenian names are fine
    assert "Խումբ 3" in validate_config(c)["groups"]


def test_save_is_validated_atomic_and_keeps_a_backup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg(playlist_id="OLD")), encoding="utf-8")
    saved = save_config(cfg(subjects=[PY, JS]), str(path))
    assert load_config(str(path)) == saved and saved["groups"]["Group-A"]["subjects"] == [PY, JS]
    assert "OLD" in (tmp_path / "config.yaml.bak").read_text()
    with pytest.raises(ValueError):
        save_config(cfg(subjects=[]), str(path))  # invalid: nothing written
    assert load_config(str(path)) == saved and not list(tmp_path.glob(".config-*"))
