"""Reject invalid local inputs before creating jobs or archive directories."""
from argparse import Namespace
from unittest.mock import Mock

import pytest

from reel import cli


@pytest.fixture
def local_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "_config", lambda: {"groups": {"Demo": {}}})
    return tmp_path


def test_unknown_group_does_not_create_job_or_inbox(local_cli):
    db = Mock()
    with pytest.raises(SystemExit, match="unknown group"):
        cli.cmd_add(db, Namespace(group="../outside", file=str(local_cli / "lesson.mp4")))
    db.create_job.assert_not_called()
    assert not (local_cli / "inbox").exists()


@pytest.mark.parametrize("name,create", [("missing.mp4", False), ("notes.txt", True)])
def test_invalid_source_does_not_create_job(local_cli, name, create):
    source = local_cli / name
    if create:
        source.write_text("synthetic fixture")
    db = Mock()
    with pytest.raises(SystemExit):
        cli.cmd_add(db, Namespace(group="Demo", file=str(source)))
    db.create_job.assert_not_called()
    assert not (local_cli / "archive").exists()


def test_resolve_rejects_unknown_target_before_opening_database(monkeypatch):
    db = Mock()
    monkeypatch.setattr(cli, "DB", db)
    monkeypatch.setattr("sys.argv", ["reel", "resolve", "1", "../other", "posted"])
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 2
    db.assert_not_called()
