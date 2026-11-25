"""Buffer ownership, ordered output and failures across raw-frame IO workers."""

from threading import Event

import pytest

from reel.frame_io import FrameIO


def test_frames_are_processed_and_written_in_order_without_buffer_reuse():
    values = iter(range(120))
    allocated, output = set(), []

    def read(buffer):
        allocated.add(id(buffer))
        value = next(values, None)
        if value is None:
            return False
        buffer[:] = bytes([value]) * 16
        return True

    def write(buffer):
        output.append(bytes(buffer))
        return len(buffer)

    with FrameIO(read, write, 16, lambda: pytest.fail("unexpected abort")) as frames:
        for index, buffer in enumerate(frames):
            assert buffer == bytes([index]) * 16
            buffer[:] = bytes([index + 1]) * 16
            frames.release(buffer)
    assert output == [bytes([index + 1]) * 16 for index in range(120)]
    assert len(allocated) == 3
    assert not frames.reader.is_alive() and not frames.writer.is_alive()


def test_reader_prefetches_while_caller_holds_previous_frame():
    second_read = Event()
    count = 0

    def read(buffer):
        nonlocal count
        count += 1
        if count > 2:
            return False
        buffer[:] = bytes([count])
        if count == 2:
            second_read.set()
        return True

    with FrameIO(read, None, 1, lambda: None) as frames:
        first = next(frames)
        assert second_read.wait(5)
        assert first == b"\x01"
        frames.release(first)
        second = next(frames)
        assert second == b"\x02"
        frames.release(second)
        with pytest.raises(StopIteration):
            next(frames)
    assert not frames.reader.is_alive()


def test_slow_writer_retains_ownership_until_the_entire_write_finishes():
    writing, release_writer = Event(), Event()
    read_count = 0
    output = []

    def read(buffer):
        nonlocal read_count
        read_count += 1
        if read_count > 10:
            return False
        buffer[:] = bytes([read_count])
        return True

    def write(buffer):
        if not output:
            writing.set()
            assert release_writer.wait(5)
        output.append(bytes(buffer))
        return len(buffer)

    with FrameIO(read, write, 1, release_writer.set) as frames:
        first = next(frames)
        frames.release(first)
        assert writing.wait(5)
        # No buffer can return to the reader until the blocked write completes.
        assert read_count <= 3
        assert first == b"\x01"
        release_writer.set()
        for buffer in frames:
            frames.release(buffer)
    assert output == [bytes([i]) for i in range(1, 11)]


@pytest.mark.parametrize("failure", ["read", "write", "short_write"])
def test_worker_failure_aborts_and_propagates_before_success(failure):
    aborted = Event()
    count = 0

    def read(buffer):
        nonlocal count
        count += 1
        if failure == "read" and count == 3:
            raise RuntimeError("truncated frame")
        buffer[:] = b"x"
        return count <= 10

    def write(buffer):
        if failure == "write":
            raise BrokenPipeError("encoder stopped")
        return 0 if failure == "short_write" else len(buffer)

    with (
        pytest.raises((RuntimeError, BrokenPipeError)),
        FrameIO(read, write, 1, aborted.set) as frames,
    ):
        for buffer in frames:
            frames.release(buffer)
    assert aborted.is_set()
    assert not frames.reader.is_alive() and not frames.writer.is_alive()


def test_processing_failure_unblocks_io_before_joining_workers():
    entered, unblock = Event(), Event()
    count = 0

    def read(buffer):
        nonlocal count
        count += 1
        if count == 1:
            buffer[:] = b"x"
            return True
        entered.set()
        assert unblock.wait(5)
        return False

    with (
        pytest.raises(ValueError, match="processing failed"),
        FrameIO(read, None, 1, unblock.set) as frames,
    ):
        next(frames)
        assert entered.wait(5)
        raise ValueError("processing failed")
    assert unblock.is_set()
    assert not frames.reader.is_alive()


def test_early_exit_cancels_a_blocked_decoder():
    reading, unblock = Event(), Event()

    def read(buffer):
        reading.set()
        assert unblock.wait(5)
        return False

    with FrameIO(read, None, 1, unblock.set) as frames:
        assert reading.wait(5)
    assert unblock.is_set()
    assert not frames.reader.is_alive()
