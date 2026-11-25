"""Encoder availability probes use synthetic input and cache the result."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reel.encoding import h264_encoder


@pytest.mark.parametrize("outcome,expected", [
    (SimpleNamespace(returncode=0), "h264_videotoolbox"),
    (SimpleNamespace(returncode=1), "libx264"),
    (OSError("unavailable"), "libx264"),
    (subprocess.TimeoutExpired("ffmpeg", 15), "libx264"),
])
def test_encoder_probe_and_cache(monkeypatch, outcome, expected):
    run = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
    monkeypatch.setattr("reel.encoding.subprocess.run", run)
    h264_encoder.cache_clear()
    try:
        assert h264_encoder() == expected
        assert h264_encoder() == expected
        assert run.call_count == 1
        command = run.call_args.args[0]
        assert command[command.index("-i") + 1].startswith("color=")
        assert run.call_args.kwargs["timeout"] == 15
    finally:
        h264_encoder.cache_clear()
