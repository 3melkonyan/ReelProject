"""Early lecturer proposals and first-answer-wins decisions, shared by web and worker."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from fastapi import HTTPException
from reel.db import DB
from reel.audit import audit_connection
from reel.i18n import t


def stamp():
    return datetime.now(timezone.utc).isoformat()


def directory(root, job_id):
    root = Path(root)
    path = root / 'work' / str(job_id)
    if (root / 'work').is_symlink() or path.is_symlink():
        raise HTTPException(409, t('review.invalid_path'))
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def locked(root, job_id):
    path = directory(root, job_id) / '.lecturer-review.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def write_choice(folder, value):
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.lecturer-choice-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, folder / 'lecturer_choice.json')
    finally:
        Path(name).unlink(missing_ok=True)


def register(database, job_id, uploader_id=None, owner_token=None):
    database.conn.execute('INSERT OR IGNORE INTO lecturer_reviews(job_id,uploader_id,owner_token) VALUES(?,?,?)',
                          (job_id, uploader_id, owner_token))


def row(database, job_id):
    return database.conn.execute('SELECT * FROM lecturer_reviews WHERE job_id=?', (job_id,)).fetchone()


def permitted(review, principal, role, owner_token=None):
    if role == 'admin':
        return True
    if role not in ('lecturer', 'uploader'):
        return False
    return bool((principal and principal.get('id') and principal['id'] == review['uploader_id'])
                or (owner_token and owner_token == review['owner_token']))


def report(root, job_id):
    path = directory(root, job_id) / 'lecturer_candidates'
    if path.is_symlink() or (path / 'auto.json').is_symlink():
        raise HTTPException(409, t('review.invalid_path'))
    try:
        data = json.loads((path / 'auto.json').read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def scan(root, job_id):
    """A durable request can be picked up after a web restart; blur can also finish it."""
    with locked(root, job_id), closing(DB(Path(root) / 'reel.db').conn) as conn:
        from types import SimpleNamespace
        db = SimpleNamespace(conn=conn)
        review = row(db, job_id)
        if not review or review['status'] in ('ready','cancelled') or review['decision']:
            return
        conn.execute("UPDATE lecturer_reviews SET status='scanning',started_at=?,error=NULL WHERE job_id=?", (time.time(), job_id))
        try:
            source = Path(conn.execute('SELECT source_path FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            from reel.pipeline import parts_of
            from reel.lecturer import quick_candidates
            # Sample the beginning across parts without waiting for the full join stage.
            if source.is_dir():
                parts = parts_of(source)
                if not parts: raise ValueError('No recording parts')
                source = parts
            quick_candidates(source, directory(root, job_id) / 'lecturer_candidates')
            if conn.execute('SELECT 1 FROM lesson_deletions WHERE job_id=?',(job_id,)).fetchone():
                conn.execute("UPDATE lecturer_reviews SET status='cancelled' WHERE job_id=?",(job_id,))
                return
            conn.execute("UPDATE lecturer_reviews SET status='ready',error=NULL WHERE job_id=?", (job_id,))
            conn.execute('INSERT OR IGNORE INTO in_app_notifications(job_id,user_id,administrators,message_key,created_at) VALUES(?,?,0,?,?)',
                         (job_id, review['uploader_id'], 'review.notification', stamp()))
            conn.execute('INSERT OR IGNORE INTO in_app_notifications(job_id,user_id,administrators,message_key,created_at) VALUES(?,NULL,1,?,?)',
                         (job_id, 'review.notification', stamp()))
        except Exception as error:
            conn.execute("UPDATE lecturer_reviews SET status='error',error=? WHERE job_id=?", (type(error).__name__, job_id))


def public(database, root, job_id):
    review = row(database, job_id)
    if not review: raise HTTPException(404, t('review.not_found'))
    candidates = report(root, job_id).get('identities', [])[:3]
    return {'job_id':job_id, 'status':review['status'], 'cursor':review['cursor'],
            'candidates':[{'rank':i, 'share':v.get('share'), 'image':f'/api/jobs/{job_id}/lecturer-review/images/identity_{i}.jpg'}
                          for i,v in enumerate(candidates, 1)],
            'decision':json.loads(review['decision']) if review['decision'] else None,
            'automatic':bool(review['automatic']), 'answered_by':review['answered_by'],
            'can_answer':not bool(review['decision']) and review['status'] != 'cancelled', 'error':review['error'],
            'applied':(database.stage_output(job_id,'blur') or {}).get('lecturer_mode')}


def _decide(database, root, job_id, action, rank, actor, role, automatic=False):
    review = row(database, job_id)
    if review['decision']: raise HTTPException(409, t('review.already_answered'))
    value = {'by':role, 'by_id':actor, 'at':stamp(), 'automatic':automatic}
    if action == 'identity':
        data = report(root, job_id)
        centres = data.get('centres', [])
        if type(rank) is not int or not 1 <= rank <= min(3, len(centres)):
            raise HTTPException(400, t('review.invalid_candidate'))
        from reel.web.lecturer_api import _centre
        value.update(identity=rank, centre=_centre(data, rank))
        if not automatic:
            target = directory(root, job_id) / 'confirmed_lecturer'
            if target.is_symlink(): raise HTTPException(409, t('review.invalid_path'))
            target.mkdir(exist_ok=True)
            source = directory(root, job_id) / 'lecturer_candidates' / str(rank)
            if source.is_symlink(): raise HTTPException(409,t('review.invalid_path'))
            for path in source.glob('ref_*'):
                if path.is_symlink(): raise HTTPException(409, t('review.invalid_path'))
                shutil.copy2(path, target / path.name)
            (target / 'identity.json').write_text(json.dumps({'centre':value['centre']}))
            database.conn.execute('INSERT OR REPLACE INTO lecturer_reference_sets VALUES(?,?,NULL)', (job_id, stamp()))
    else:
        value['none'] = True
    # A crash after the atomic file replacement is recovered by the blur resolver below.
    write_choice(directory(root, job_id), value)
    decision = {'none':True} if value.get('none') else {'identity':rank}
    database.conn.execute('UPDATE lecturer_reviews SET decision=?,answered_by=?,answered_role=?,answered_at=?,automatic=? WHERE job_id=?',
                          (json.dumps(decision), actor, role, value['at'], int(automatic), job_id))
    audit_connection(database.conn, actor, 'lecturer.answer', f'job:{job_id}',
                     {'manual':not automatic,'role':role,'target':action,'count':rank if action == 'identity' else 0})
    return decision


def _recover_choice(database, root, job_id):
    """The atomic choice file survives a crash before the database commit."""
    path = directory(root, job_id) / 'lecturer_choice.json'
    if not path.is_file() or path.is_symlink():
        return False
    saved = json.loads(path.read_text())
    decision = {'none': True} if saved.get('none') else {'identity': saved['identity']}
    database.conn.execute('UPDATE lecturer_reviews SET decision=?,answered_by=?,answered_role=?,answered_at=?,automatic=? WHERE job_id=?',
        (json.dumps(decision), saved.get('by_id'), saved.get('by'), saved.get('at'), int(saved.get('automatic', False)), job_id))
    if not saved.get('automatic') and not saved.get('none'):
        references = directory(root, job_id) / 'confirmed_lecturer'
        if references.is_dir() and not references.is_symlink():
            database.conn.execute('INSERT OR IGNORE INTO lecturer_reference_sets VALUES(?,?,NULL)', (job_id, saved.get('at') or stamp()))
    audit_connection(database.conn, saved.get('by_id'), 'lecturer.answer', f'job:{job_id}',
                     {'manual': not saved.get('automatic', False), 'recovered': True})
    return True


def answer(database, root, job_id, body, actor=None, role='admin'):
    if not isinstance(body, dict) or len(body) != 1 or next(iter(body)) not in ('identity','none','next'):
        raise HTTPException(400, t('review.invalid_answer'))
    with locked(root, job_id):
        database.conn.execute('BEGIN IMMEDIATE')
        try:
            review = row(database, job_id)
            if not review or review['decision'] or database.conn.execute('SELECT 1 FROM lesson_deletions WHERE job_id=?',(job_id,)).fetchone(): raise HTTPException(409, t('review.already_answered'))
            if _recover_choice(database, root, job_id):
                database.conn.execute('COMMIT')
                raise HTTPException(409, t('review.already_answered'))
            action = next(iter(body))
            if action == 'next':
                if body[action] is not True: raise HTTPException(400, t('review.invalid_answer'))
                count = len(report(root, job_id).get('identities', [])[:3])
                database.conn.execute('UPDATE lecturer_reviews SET cursor=? WHERE job_id=?', (review['cursor'] % max(1,count) + 1, job_id))
                audit_connection(database.conn, actor, 'lecturer.next', f'job:{job_id}', {})
                decision = None
            else:
                if action == 'none' and body[action] is not True: raise HTTPException(400, t('review.invalid_answer'))
                decision = _decide(database, root, job_id, action, body.get('identity'), actor, role)
            database.conn.execute('COMMIT')
            return {'choice':decision}
        except BaseException:
            database.conn.rollback(); raise


def resolve_for_blur(ctx):
    """Freeze a human answer or the top proposal at blur entry; never wait for a person."""
    if not row(ctx.db, ctx.job_id): return None
    scan(ctx.workdir.parent.parent, ctx.job_id)
    root = ctx.workdir.parent.parent
    with locked(root, ctx.job_id):
        ctx.db.conn.execute('BEGIN IMMEDIATE')
        try:
            review = row(ctx.db, ctx.job_id)
            path = directory(root, ctx.job_id) / 'lecturer_choice.json'
            if not review['decision'] and not _recover_choice(ctx.db, root, ctx.job_id):
                candidates = report(root, ctx.job_id).get('identities', [])
                _decide(ctx.db, root, ctx.job_id, 'identity' if candidates else 'none', 1, None, 'automatic', True)
            ctx.db.conn.execute('COMMIT')
            return json.loads(path.read_text())
        except BaseException:
            ctx.db.conn.rollback(); raise
