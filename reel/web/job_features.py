"""Upload review, local reference inventory and per-user job notifications."""
from contextlib import closing
import os
from pathlib import Path
import re
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from reel import lecturer_review as reviews
from reel.i18n import t, language_context
from reel.audit import audit_connection


def send_review_mail(root, address, job_id):
    if os.environ.get('REEL_DRY_RUN') == '1': return True
    from reel import brand_mail
    from reel.language import recipient_language
    with language_context(recipient_language(root, address)):
        return brand_mail.send(root, address, t('review.mail_subject'), eyebrow='Reel',
                               title=t('review.title'), paragraphs=[t('review.mail_body', job=job_id)])


def process_review(root, job_id):
    from reel.db import DB
    with closing(DB(root/'reel.db').conn) as conn:
        if conn.execute('SELECT 1 FROM lesson_deletions WHERE job_id=?',(job_id,)).fetchone(): return
    reviews.scan(root, job_id)
    with closing(DB(root / 'reel.db').conn) as conn:
        from types import SimpleNamespace
        row = reviews.row(SimpleNamespace(conn=conn), job_id)
        if not row or row['status'] != 'ready' or row['notified']: return
        from reel.notifications import once
        users = conn.execute("SELECT id,email FROM users WHERE active=1 AND removed_at IS NULL AND deleted_at IS NULL AND (role='admin' OR id=?)", (row['uploader_id'],)).fetchall()
        for user in users:
            once(conn, f'lecturer-choice:{job_id}', user['email'], 'email',
                 lambda user=user: send_review_mail(root, user['email'], job_id))
        conn.execute('UPDATE lecturer_reviews SET notified=1 WHERE job_id=?', (job_id,))


def make_router(env):
    router = APIRouter()
    def allowed(request, database, job_id):
        review = reviews.row(database, job_id)
        if not review: raise HTTPException(404, t('review.not_found'))
        principal = getattr(request.state, 'principal', None)
        if not reviews.permitted(review, principal, request.session.get('role'), request.session.get('upload_owner')):
            raise HTTPException(403, t('review.forbidden'))
        return review
    @router.get('/api/jobs/{job_id}/lecturer-review')
    def review(job_id:int, request:Request):
        database=env['db']()
        with closing(database.conn):
            allowed(request,database,job_id)
            return reviews.public(database,env['ROOT'],job_id)
    @router.post('/api/jobs/{job_id}/lecturer-review')
    async def answer(job_id:int, request:Request):
        try:
            raw=await request.body()
            if len(raw)>1024: raise ValueError()
            import json
            body=json.loads(raw)
        except ValueError: raise HTTPException(400,t('review.invalid_answer')) from None
        def save():
            database=env['db']()
            with closing(database.conn):
                allowed(request,database,job_id)
                actor=(getattr(request.state,'principal',None) or {}).get('id')
                return reviews.answer(database,env['ROOT'],job_id,body,actor,request.session.get('role'))
        return await run_in_threadpool(save)
    @router.get('/api/jobs/{job_id}/lecturer-review/images/{name}')
    def candidate_image(job_id:int, name:str, request:Request):
        database=env['db']()
        with closing(database.conn): allowed(request,database,job_id)
        if not re.fullmatch(r'identity_[1-3]\.jpg',name): raise HTTPException(404,t('lecturer_api.image_not_found'))
        path=reviews.directory(env['ROOT'],job_id)/'lecturer_candidates'/name
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file(): raise HTTPException(404,t('lecturer_api.image_not_found'))
        return Response(path.read_bytes(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    @router.get('/api/notifications')
    def notifications(request:Request):
        env['authenticated'](request)
        principal=getattr(request.state,'principal',None) or {}
        database=env['db']()
        with closing(database.conn):
            rows=database.conn.execute('SELECT n.* FROM in_app_notifications n JOIN lecturer_reviews r ON r.job_id=n.job_id WHERE r.decision IS NULL AND NOT EXISTS (SELECT 1 FROM lesson_deletions d WHERE d.job_id=n.job_id) AND ((n.administrators=1 AND ?=1) OR (n.user_id=? AND n.user_id IS NOT NULL) OR (r.owner_token=? AND r.owner_token IS NOT NULL)) ORDER BY n.id DESC LIMIT 50',
                (int(request.session.get('role')=='admin'),principal.get('id'),request.session.get('upload_owner'))).fetchall()
            return {'items':[{'id':r['id'],'job_id':r['job_id'],'message':t(r['message_key'],job=r['job_id'])} for r in rows]}
    def administrator(request):
        env['authenticated'](request)
        if request.session.get('role')!='admin': raise HTTPException(403,t('review.forbidden'))
    @router.post('/api/jobs/{job_id}/delete')
    async def delete_lesson(job_id:int,request:Request):
        administrator(request)
        import json
        try:
            raw=await request.body()
            if len(raw)>1024: raise ValueError()
            body=json.loads(raw)
            if not isinstance(body,dict) or body.get('confirm') is not True or set(body)-{'confirm','slack_channel'}: raise ValueError()
            channel=body.get('slack_channel')
            if channel is not None and (not isinstance(channel,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',channel)): raise ValueError()
        except ValueError: raise HTTPException(400,t('deletion.confirm_required')) from None
        def remove():
            from reel.lesson_deletion import request as retire
            database=env['db']()
            with closing(database.conn):
                try: return retire(database,env['ROOT'],job_id,(getattr(request.state,'principal',None) or {}).get('id'),channel)
                except KeyError: raise HTTPException(404,t('app.lesson_not_found')) from None
        return await run_in_threadpool(remove)
    @router.get('/api/jobs/{job_id}/deletion')
    def deletion_state(job_id:int,request:Request):
        administrator(request)
        from reel.lesson_deletion import state
        database=env['db']()
        with closing(database.conn):
            try: database.job(job_id)
            except KeyError: raise HTTPException(404,t('app.lesson_not_found')) from None
            return state(database,job_id) or {'state':'not_requested','steps':{},'error':None}
    @router.get('/api/privacy/lecturer-references')
    def reference_list(request:Request):
        administrator(request)
        database=env['db']()
        with closing(database.conn):
            rows=database.conn.execute('SELECT r.job_id,r.created_at,j.grp FROM lecturer_reference_sets r JOIN jobs j ON j.id=r.job_id WHERE r.deleted_at IS NULL ORDER BY r.created_at DESC').fetchall()
            return {'items':[dict(r) for r in rows]}
    @router.delete('/api/privacy/lecturer-references/{job_id}')
    def delete_references(job_id:int,request:Request):
        administrator(request)
        with reviews.locked(env['ROOT'],job_id):
            database=env['db']()
            with closing(database.conn):
                if not database.conn.execute('SELECT 1 FROM lecturer_reference_sets WHERE job_id=?',(job_id,)).fetchone(): raise HTTPException(404,t('review.not_found'))
                path=reviews.directory(env['ROOT'],job_id)/'confirmed_lecturer'
                if path.is_symlink(): raise HTTPException(409,t('review.invalid_path'))
                shutil.rmtree(path,ignore_errors=False) if path.exists() else None
                # Remove the saved descriptor too. A pre-blur deletion opts out of exemption.
                choice=path.parent/'lecturer_choice.json'
                if choice.is_symlink(): raise HTTPException(409,t('review.invalid_path'))
                reviews.write_choice(path.parent,{'none':True,'by':'admin','at':reviews.stamp(),'reference_deleted':True})
                database.conn.execute('UPDATE lecturer_reference_sets SET deleted_at=? WHERE job_id=?',(reviews.stamp(),job_id))
                actor=(getattr(request.state,'principal',None) or {}).get('id')
                audit_connection(database.conn,actor,'lecturer.reference_delete',f'job:{job_id}',{})
        return {'ok':True}
    return router
