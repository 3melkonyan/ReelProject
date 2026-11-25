"""Role-scoped library/statistics and privacy-safe transition previews."""
from __future__ import annotations
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import tempfile
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from reel.insights import library_records, statistics, cut_summary
from reel.web import accounts as account_auth

_ENCODE = threading.Lock()


def make_router(env):
    router = APIRouter(prefix='/api')
    def reader(request: Request):
        who = env['authenticated'](request)
        if who not in ('admin', 'viewer', 'lecturer'): raise HTTPException(403, 'reader only')
        return who
    def library_reader(request: Request):
        who = env['authenticated'](request)
        if who not in ('admin','viewer','student','lecturer'): raise HTTPException(403, 'library only')
        return who
    def summary(job_id):
        d = env['db']()
        try: d.job(job_id)
        except KeyError: raise HTTPException(404, 'not found') from None
        plan = d.stage_output(job_id, 'segments')
        if not plan or 'cuts' not in plan:
            file = env['WORK']/str(job_id)/'segments.json'
            plan = env['_read_json'](file) or plan
        if not plan: return {'available':False, 'message':'Տվյալ չկա'}
        if 'cut_summary' in plan: return {'available':True, **plan['cut_summary']}
        transcript = env['_read_json'](env['WORK']/str(job_id)/'transcript'/'transcript.json')
        return {'available':True, **cut_summary(plan, transcript, d.stage_output(job_id, 'join')),
                'details_available':'cuts' in plan}
    @router.get('/library')
    def library(_: str = Depends(library_reader)):
        from reel.library_view import library_entries
        database=env['db']()
        with closing(database.conn):
            return library_entries(database, env['ROOT']/'library.jsonl', groups=account_auth.groups())
    @router.get('/stats')
    def stats(_: str = Depends(reader)):
        from reel.config import load_config
        cfg = load_config(str(env['ROOT']/'config.yaml'))
        result = statistics(env['db'](), cfg.get('time_savings'), groups=account_auth.groups())
        labels = dict(env['STAGES'])
        for item in result['slow_stages']:
            item['label'] = labels.get(item['stage'], item['stage'])
        return result
    @router.get('/jobs/{job_id}/cuts')
    def cuts(job_id: int, _: str = Depends(reader)):
        return summary(job_id)
    def location(job_id, index):
        data = summary(job_id)
        cuts = data.get('cuts', [])
        if index < 0 or index >= len(cuts): raise HTTPException(404, 'cut not found')
        if not env['_privacy_passed'](job_id): raise HTTPException(403, 'Գաղտնիության ստուգումը դեռ չի ավարտվել։')
        root = env['WORK'].resolve(); source = env['WORK']/str(job_id)/'final_blurred.mp4'
        # Fixed filename only; reject symlinks at every descendant component.
        if source.parent.is_symlink() or source.is_symlink() or not source.resolve().is_relative_to(root):
            raise HTTPException(403, 'unsafe media path')
        if not source.is_file(): raise HTTPException(410, 'Վերջնական մշուշված ֆայլը հեռացված է․ անցման preview-ն անհասանելի է։')
        point = float(cuts[index]['final_boundary']); final = float(data.get('final_sec') or 0)
        start = max(0, min(point-5, max(0, final-10)))
        key = hashlib.sha256(f'{source.stat().st_mtime_ns}:{source.stat().st_size}:{start}'.encode()).hexdigest()[:24]
        cache = env['ROOT']/'out'/'transition-previews'/str(job_id)
        for folder in (env['ROOT']/'out', cache.parent, cache):
            if folder.is_symlink(): raise HTTPException(403, 'unsafe cache path')
        cache.mkdir(parents=True, exist_ok=True)
        destination = cache/f'{key}.mp4'
        if destination.is_symlink(): raise HTTPException(403, 'unsafe cache file')
        return source, destination, start
    def encode(source, destination, start):
        if destination.is_file() and not destination.is_symlink(): return
        if not _ENCODE.acquire(blocking=False): raise HTTPException(409, 'Մեկ այլ preview է պատրաստվում․ կրկին փորձիր։')
        lock = None; tmp = None
        try:
            lock = os.open(destination.parent.parent/'.encode.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
            try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise HTTPException(409, 'Preview-ն զբաղված է․ կրկին փորձիր։') from None
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.part.mp4', delete=False) as handle:
                tmp = Path(handle.name)
            subprocess.run(['nice','-n','15','ffmpeg','-nostdin','-v','error','-y','-ss',str(start),'-i',str(source),
                            '-t','10','-vf','scale=-2:480','-c:v','libx264','-preset','veryfast','-threads','1',
                            '-c:a','aac','-movflags','+faststart',str(tmp)], capture_output=True, check=True, timeout=90)
            tmp.replace(destination)
        except subprocess.TimeoutExpired: raise HTTPException(504, 'Preview-ի պատրաստման ժամանակը սպառվեց։') from None
        except subprocess.CalledProcessError: raise HTTPException(500, 'Preview-ն պատրաստել չհաջողվեց։') from None
        finally:
            if tmp is not None: tmp.unlink(missing_ok=True)
            if lock is not None: os.close(lock)
            _ENCODE.release()
    @router.post('/jobs/{job_id}/cuts/{index}/preview')
    async def create_preview(job_id: int, index: int, request: Request, _: str = Depends(reader)):
        if request.headers.get('x-requested-with') != 'reel': raise HTTPException(400, 'bad request')
        source, destination, start = location(job_id, index)
        await run_in_threadpool(encode, source, destination, start)
        return {'url':f'/api/jobs/{job_id}/cuts/{index}/preview','start':start,'duration':10}
    @router.get('/jobs/{job_id}/cuts/{index}/preview')
    def preview(job_id: int, index: int, _: str = Depends(reader)):
        _, destination, _ = location(job_id, index)
        if destination.is_symlink() or not destination.is_file(): raise HTTPException(404, 'Նախ պատրաստիր preview-ն։')
        return FileResponse(destination, media_type='video/mp4', headers={'Cache-Control':'private, no-store'})
    return router
