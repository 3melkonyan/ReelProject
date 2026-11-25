"""Cross-process worker ownership and cooperative cancellation before lesson cleanup."""
from contextlib import contextmanager
import fcntl
import os
from reel.lecturer_review import directory


class JobCancelled(BaseException):
    """Cancellation bypasses stage retries and optional-stage exception fallbacks."""


def check_cancel(database, job_id):
    if database is not None and getattr(database, 'conn', None) is not None:
        if database.conn.execute('SELECT 1 FROM lesson_deletions WHERE job_id=?', (job_id,)).fetchone():
            raise JobCancelled()


@contextmanager
def job_lock(root, job_id, name='worker', wait=True):
    path = directory(root, job_id) / ('.' + name + '.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
        else:
            yield True
