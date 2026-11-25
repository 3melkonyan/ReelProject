"""Read-only media observations and throttled, durable job progress (no media decisions)."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import math
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

_ACTIVE = ContextVar('reel_progress', default=None)
# Seconds per source minute: NOTES real lesson measurements; small stages are priors.
RATES = dict(join=.6, probe=.03, audio=.2, transcribe=11, segments=1.3, render=6.1,
             blur=18, privacy=3.4, preview=2.76, metadata=1, thumbnail=.1,
             youtube_upload=2, playlist=.02, thumbnail_set=.02, slack_post=.1,
             classroom_post=.02, library_add=.01)


def seconds(a, b):
    try:
        return max(0, (datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds())
    except (ValueError, TypeError):
        return 0


def weights(db, duration, names):
    samples = {n: [] for n in names}
    for job in db.conn.execute("SELECT id FROM jobs WHERE status='done' ORDER BY id DESC LIMIT 20"):
        publication = db.publication(job['id'], 'youtube')
        if publication and str(publication['external_id'] or '').startswith('dry'):
            continue
        dur = (db.stage_output(job['id'], 'probe') or {}).get('duration_sec', 0)
        if not dur or dur <= 0:
            continue
        for st in db.stages(job['id']):
            elapsed = seconds(st['started_at'], st['finished_at'])
            if st['name'] in samples and st['status'] == 'done' and elapsed > 0:
                samples[st['name']].append(elapsed / (dur/60))
    return {n: max(300 if n == 'slack_post' else .1,
                   (statistics.median(samples[n]) if samples[n] else RATES.get(n, .1))*duration/60)
            for n in names}


def ffmpeg_seconds(text):
    value = None
    for line in text.splitlines():
        key, _, raw = line.partition('=')
        try:
            if key in ('out_time_us', 'out_time_ms'):
                value = float(raw)/1_000_000  # ffmpeg's ms key also uses microseconds
            elif key == 'out_time':
                h, m, s = map(float, raw.split(':')); value = h*3600+m*60+s
        except ValueError:
            pass
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def byte_fraction(sent, total):
    try:
        value = float(sent)/float(total)
        return min(1., max(0., value)) if math.isfinite(value) and float(total) > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def report(current, total=1, *, kind=None):
    sink = _ACTIVE.get()
    fraction = byte_fraction(current, total)
    if sink is not None and fraction is not None:
        if kind == 'faces' and sink.stage == 'blur':
            fraction *= .18
        elif kind == 'encode' and sink.stage == 'blur':
            fraction = .2 + .78*fraction
        sink.observe(fraction)


class Tracker:
    def __init__(self, db, job_id, names):
        self.db, self.job_id, self.names = db, job_id, names
        self.data = db.insights(job_id)
        self.state = self.data.get('progress', {})
        self.stage = ''; self.last_write = 0; self.began = time.monotonic()
        self.state.setdefault('started_at', datetime.now(timezone.utc).isoformat())

    @property
    def duration(self):
        original = (self.db.stage_output(self.job_id, 'probe') or {}).get('duration_sec', 0)
        if self.stage in ('blur', 'privacy', 'preview', 'youtube_upload'):
            return (self.db.stage_output(self.job_id, 'segments') or {}).get('final_sec') or original
        return original

    @contextmanager
    def scope(self, name):
        self.stage = name; self.began = time.monotonic()
        self.observe(0, force=True)
        token = _ACTIVE.set(self)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    def check_cancel(self):
        from reel.job_lifecycle import check_cancel
        check_cancel(self.db,self.job_id)

    def observe(self, fraction=0, force=False):
        if time.monotonic()-getattr(self,'last_cancel_check',0) >= .5 or force:
            self.check_cancel()
            self.last_cancel_check=time.monotonic()
        # Observability must never turn successful media work into a failed job.
        try:
            self._observe(fraction, force)
        except Exception:
            import logging
            logging.getLogger('reel').debug('Progress persistence unavailable', exc_info=True)

    def _observe(self, fraction, force):
        now = time.monotonic()
        if not force and now-self.last_write < 2:
            return
        duration = (self.db.stage_output(self.job_id, 'probe') or {}).get('duration_sec', 0)
        if 'weights' not in self.state and duration > 0:
            self.state['weights'] = weights(self.db, duration, self.names)
            self.state['predicted_duration_sec'] = sum(self.state['weights'].values())
        w = self.state.get('weights', {})
        stages = {s['name']: s['status'] for s in self.db.stages(self.job_id)}
        done = sum(v for n,v in w.items() if stages.get(n) == 'done')
        total = sum(w.values())
        fraction = min(.999, max(0, fraction))
        fraction = max(self.state.get('stage_fraction', 0) if self.state.get('stage') == self.stage else 0, fraction)
        active = w.get(self.stage, 0) if stages.get(self.stage) != 'done' else 0
        percent = min(99.9, 100*(done+active*fraction)/total) if total else 0
        remaining = None
        if total:
            current = active*(1-fraction)
            if .02 < fraction < .999 and active:
                current = (now-self.began)*(1-fraction)/fraction
            estimate = max(0, total-done-active+current)
            old = self.state.get('eta_seconds')
            remaining = .75*old+.25*estimate if old is not None and self.state.get('stage') == self.stage else estimate
        complete = self.db.job(self.job_id)['status'] == 'done'
        self.state.update(percent=100. if complete else max(self.state.get('percent', 0), percent),
                          stage=self.stage, stage_fraction=fraction, eta_seconds=0 if complete else remaining,
                          updated_at=datetime.now(timezone.utc).isoformat())
        if complete:
            actual = seconds(self.state['started_at'], self.state['updated_at'])
            self.state['actual_duration_sec'] = actual
            if 'predicted_duration_sec' in self.state:
                self.state['eta_error_sec'] = abs(actual-self.state['predicted_duration_sec'])
        self.data['progress'] = self.state
        # Stage changes update memory immediately, but coalesce short stages into one write.
        if not complete and self.last_write and now-self.last_write < 2:
            return
        self.db.save_insights(self.job_id, self.data); self.last_write = now


def observed_run(cmd, *, run=subprocess.run, total=None, **kwargs):
    """Same subprocess result/errors; ffmpeg progress is read on the worker thread.

    Inactive calls take the original path, preserving CLI and test contracts.
    A temporary progress file avoids interfering with media stdout/stderr.
    """
    sink = _ACTIVE.get()
    encoder_index = 3 if len(cmd) > 3 and list(cmd[:3]) == ['nice', '-n', '10'] else 0
    if sink is None or not cmd or Path(str(cmd[encoder_index])).name != 'ffmpeg':
        return run(cmd, **kwargs)
    expected = total or sink.duration
    with tempfile.TemporaryDirectory(prefix='reel-progress-') as directory:
        progress = Path(directory)/'progress'
        args = [*cmd[:encoder_index + 1], '-progress', str(progress), *cmd[encoder_index + 1:]]
        options = dict(kwargs); check = options.pop('check', False)
        timeout = options.pop('timeout', None)
        if options.pop('capture_output', False):
            options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started = time.monotonic()
        with subprocess.Popen(args, **options) as proc:
            try:
                while True:
                    try:
                        out, err = proc.communicate(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        if hasattr(sink, "check_cancel"):
                            sink.check_cancel()
                        if timeout and time.monotonic()-started >= timeout:
                            raise subprocess.TimeoutExpired(args, timeout) from None
                        value = ffmpeg_seconds(read_progress(progress))
                        if value is not None and expected:
                            # Render includes audio measurement / validation passes; only encode advances.
                            encoding = any(str(x).endswith(('.mp4', '.mkv', '.mov')) for x in args[-1:])
                            if sink.stage != 'render' or encoding:
                                report(value, expected)
            except BaseException:
                proc.kill(); proc.communicate(); raise
        result = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        if check:
            result.check_returncode()
        return result


@contextmanager
def whisper_observer(fn):
    """Observe MLX Whisper's existing seek/frame updates without changing transcription."""
    namespace = getattr(fn, '__globals__', {})
    original = namespace.get('tqdm')
    if _ACTIVE.get() is None or original is None:
        yield; return
    from types import SimpleNamespace
    def factory(*args, **kwargs):
        bar = original.tqdm(*args, **kwargs)
        update = bar.update; count = 0
        def tracked(delta=1):
            nonlocal count
            count += delta; report(count, kwargs.get('total', 0))
            return update(delta)
        bar.update = tracked
        return bar
    namespace['tqdm'] = SimpleNamespace(**{**vars(original), 'tqdm': factory})
    try:
        yield
    finally:
        namespace['tqdm'] = original

class EncoderChannel:
    """An optional ffmpeg side channel; polling is bounded even in a per-frame loop."""
    def __init__(self, duration):
        self.duration = duration; self.file = None; self.last = 0
        self.args = []
    def __enter__(self):
        if _ACTIVE.get() is not None:
            try:
                self.file = tempfile.NamedTemporaryFile(prefix='reel-encode-')
                self.args = ['-progress', self.file.name]
            except OSError:
                pass
        return self
    def poll(self):
        if self.file is None or time.monotonic()-self.last < 2: return
        self.last = time.monotonic()
        try:
            value = ffmpeg_seconds(read_progress(Path(self.file.name)))
        except OSError:
            return
        if value is not None: report(value, self.duration, kind='encode')
    def __exit__(self, *args):
        if self.file is not None: self.file.close()


def observed_sleep(duration, *, sleep=time.sleep, fraction=lambda:0):
    """Keep the same wait, sample elapsed processing wait only while a worker observes it."""
    if _ACTIVE.get() is None:
        return sleep(duration)
    deadline = time.monotonic()+duration
    while (remaining := deadline-time.monotonic()) > 0:
        sleep(min(2, remaining))
        report(min(.95, fraction()))


def processing_fraction(elapsed):
    sink = _ACTIVE.get()
    expected = sink.state.get('weights', {}).get('slack_post', 300) if sink else 300
    return min(.95, max(0, elapsed)/max(expected, 1))


def read_progress(path):
    """Bound IO and parsing cost for multi-hour ffmpeg jobs."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2); end = stream.tell()
            stream.seek(max(0, end-8192))
            return stream.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def media_seconds(probe):
    try:
        value = float(probe.get('format', {}).get('duration') or 0)
        return value if math.isfinite(value) and value > 0 else 0
    except (ValueError, TypeError):
        return 0
