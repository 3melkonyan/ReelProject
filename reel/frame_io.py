"""Overlap raw frame IO with processing using a fixed pool of owned buffers."""
from queue import Empty, Queue
from threading import Event, Lock, Thread


class FrameIO:
    """Read and write in order while the caller processes one frame at a time.

    A buffer belongs to exactly one of the reader, caller, or writer. It returns
    to the reader only after its complete write, so detection and pixelation can
    safely operate in place. Three buffers bound memory regardless of duration.
    With no writer, release returns buffers directly to the reader (two buffers).
    ``abort`` must unblock both streams (normally by terminating their children).
    """
    def __init__(self, read_frame, write_frame, frame_size, abort):
        self.read_frame, self.write_frame, self.abort = read_frame, write_frame, abort
        self.free, self.decoded, self.pending = Queue(), Queue(), Queue()
        for _ in range(3 if write_frame is not None else 2):
            self.free.put(bytearray(frame_size))
        self.stopped = Event()
        self.finished = Event()
        self.error = None
        self.lock = Lock()
        self.reader = Thread(target=self._read, name="frame-reader", daemon=True)
        self.writer = Thread(target=self._write, name="frame-writer", daemon=True)

    def _check(self):
        if self.error is not None:
            raise self.error

    def _get(self, queue):
        while not self.stopped.is_set():
            self._check()
            try:
                return queue.get(timeout=0.05)
            except Empty:
                pass
        return None

    def _fail(self, error):
        with self.lock:
            if self.error is None:
                self.error = error

    def _read(self):
        try:
            while (buffer := self._get(self.free)) is not None:
                if not self.read_frame(buffer):
                    self.finished.set()
                    self.decoded.put(None)
                    return
                self.decoded.put(buffer)
        except BaseException as error:
            self._fail(error)

    def _write(self):
        try:
            while (buffer := self._get(self.pending)) is not None:
                count = self.write_frame(buffer)
                if count != len(buffer):
                    raise BrokenPipeError("Incomplete raw video frame write")
                self.free.put(buffer)
        except BaseException as error:
            self._fail(error)

    def __enter__(self):
        self.reader.start()
        try:
            if self.write_frame is not None:
                self.writer.start()
        except BaseException:
            self.stopped.set()
            self.abort()
            self.reader.join()
            raise
        return self

    def __iter__(self):
        return self

    def __next__(self):
        buffer = self._get(self.decoded)
        if buffer is None:
            self._check()
            raise StopIteration
        return buffer

    def release(self, buffer):
        self._check()
        if self.write_frame is None:
            self.free.put(buffer)
        else:
            self.pending.put(buffer)

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self._check()
                if self.write_frame is not None:
                    self.pending.put(None)
                while self.writer.is_alive():
                    self.writer.join(timeout=0.05)
                    self._check()
                self._check()
        finally:
            self.stopped.set()
            # On failure a worker may be blocked inside read/write, rather than
            # waiting on a queue. Stop the children before joining those workers.
            if exc_type is not None or self.error is not None or not self.finished.is_set():
                self.abort()
            self.reader.join()
            if self.write_frame is not None:
                self.writer.join()
