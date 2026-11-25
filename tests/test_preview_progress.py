"""Low-priority preview encoding retains worker progress observation."""

from types import SimpleNamespace
from unittest.mock import Mock

from reel import progress


def test_nice_wrapped_ffmpeg_keeps_progress_instrumentation(monkeypatch):
    process = Mock()
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    process.communicate.return_value = (b"", b"")
    process.returncode = 0
    spawn = Mock(return_value=process)
    monkeypatch.setattr(progress.subprocess, "Popen", spawn)
    fallback = Mock()
    token = progress._ACTIVE.set(SimpleNamespace(duration=10, stage="preview"))
    command = ["nice", "-n", "10", "ffmpeg", "-i", "synthetic.mp4", "preview.mp4"]
    try:
        result = progress.observed_run(command, run=fallback, check=True, capture_output=True)
    finally:
        progress._ACTIVE.reset(token)
    actual = spawn.call_args.args[0]
    assert actual[:5] == ["nice", "-n", "10", "ffmpeg", "-progress"]
    assert actual[6:] == command[4:]
    assert result.returncode == 0
    fallback.assert_not_called()
