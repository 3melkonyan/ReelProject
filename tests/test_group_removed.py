"""A lesson whose group was removed in Settings stops at the first stage."""

import pytest

from reel import hints
from reel.pipeline import Ctx, StopJob
from reel.stages import join_stage


def test_join_stops_when_the_group_is_gone(tmp_path):
    ctx = Ctx(
        None, 1, tmp_path / "lesson.mp4", tmp_path / "work", "Gone", {"groups": {"Group-A": {}}}
    )
    with pytest.raises(StopJob, match="no longer in config.yaml"):
        join_stage(ctx)
    assert "Վերականգնիր" in hints.hint(
        "[join] StopJob: group 'Gone' is no longer in config.yaml — x"
    )


def test_join_still_passes_a_single_file_through(tmp_path):
    ctx = Ctx(
        None, 1, tmp_path / "lesson.mp4", tmp_path / "work", "Group-A", {"groups": {"Group-A": {}}}
    )
    assert join_stage(ctx) == {"parts": 1}
