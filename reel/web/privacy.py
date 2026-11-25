"""Versioned recording consent and operator-reviewed data requests. No automatic media deletion."""
from __future__ import annotations

from reel.i18n import t as _t
import json,re,time
from contextlib import closing
from fastapi import APIRouter,HTTPException,Request

VERSION='2026-11-20'
NOTICE_KEY='privacy.lessons_are_recorded_and_faces_are_blurred'
POLICY_KEY='privacy.reel_processes_lecture_recordings_for_your_organization'


def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS recording_consents(user_id INTEGER PRIMARY KEY REFERENCES users(id), policy_version TEXT NOT NULL, accepted_at REAL NOT NULL, guardian_name TEXT, guardian_email TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS data_requests(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT \'open\', resolved_at REAL, resolved_by INTEGER)')


def junior(payload,conn=None):
    if conn is not None and payload.get("role")=="student":
        for name in payload.get("groups",[]):
            row=conn.execute("SELECT course_id FROM group_course_links WHERE name=?",(name,)).fetchone()
            if row and row[0]=="ai-junior":return True
    return payload.get('role')=='student' and any(re.search(r'ai[\s_-]*junior',g,re.I) for g in payload.get('groups',[]))


def validate(data,payload,conn=None):
    if data.get('recording_consent') is not True:raise HTTPException(400,_t('privacy.accept_the_recording_and_privacy_terms'))
    name=email=None
    if junior(payload,conn):
        name=str(data.get('guardian_name') or '').strip();email=str(data.get('guardian_email') or '').strip()
        if data.get('guardian_consent') is not True or not 2<=len(name)<=120 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:
            raise HTTPException(400,_t('privacy.ai_junior_requires_a_parent_s_or'))
    return dict(version=VERSION,guardian_name=name,guardian_email=email)


def record(conn,uid,consent):
    conn.execute('INSERT INTO recording_consents VALUES(?,?,?,?,?)',(uid,consent['version'],time.time(),consent['guardian_name'],consent['guardian_email']))
    from reel.audit import audit_connection
    audit_connection(conn,uid,'privacy.consent','user:'+str(uid),{'status':'accepted','reason':'guardian' if consent['guardian_name'] else 'recording'})


def make_router(env):
    router=APIRouter(prefix='/api/privacy')
    @router.get('/policy')
    def policy():return dict(version=VERSION,notice=_t(NOTICE_KEY),paragraphs=_t(POLICY_KEY).splitlines())
    def person(request):
        p=getattr(request.state,'principal',None)
        if not p or not p.get('id'):raise HTTPException(401,_t('access.sign_in_with_a_personal_account'))
        return p
    @router.post('/requests')
    async def request_deletion(request:Request):
        p=person(request)
        if int(request.headers.get('content-length') or 0)>4096:raise HTTPException(413,_t('account_api.request_too_large'))
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:raise HTTPException(413,_t('account_api.request_too_large'))
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
        except (ValueError,UnicodeError):raise HTTPException(400,_t('access.invalid_request')) from None
        message=str(data.get('message') or '')
        if not 5<=len(message)<=2000:raise HTTPException(400,_t('privacy.describe_your_request_in_5_2000_characters'))
        with closing(env['db']().conn) as conn:
            ensure(conn);conn.execute('BEGIN IMMEDIATE')
            try:
                row=conn.execute("SELECT id FROM data_requests WHERE user_id=? AND status='open'",(p['id'],)).fetchone()
                ident=row[0] if row else conn.execute('INSERT INTO data_requests(user_id,created_at,message) VALUES(?,?,?)',(p['id'],time.time(),message)).lastrowid
                conn.execute('COMMIT')
            except BaseException:conn.execute('ROLLBACK');raise
        return {'id':ident,'message':_t('privacy.your_request_has_been_saved_an_administrator')}
    @router.get('/requests')
    def requests(request:Request):
        p=person(request)
        with closing(env['db']().conn) as conn:
            ensure(conn)
            sql='SELECT id,user_id,created_at,message,status,resolved_at FROM data_requests'
            return [dict(r) for r in conn.execute(sql+('' if p['role']=='admin' else ' WHERE user_id=?')+' ORDER BY id DESC LIMIT 200',() if p['role']=='admin' else (p['id'],))]
    @router.post('/requests/{ident}/resolve')
    def resolve(ident:int,request:Request):
        p=person(request)
        if p['role']!='admin':raise HTTPException(403,_t('account_api.administrators_only'))
        with closing(env['db']().conn) as conn:
            ensure(conn);cur=conn.execute("UPDATE data_requests SET status='resolved',resolved_at=?,resolved_by=? WHERE id=? AND status='open'",(time.time(),p['id'],ident))
        if not cur.rowcount:raise HTTPException(404,_t('privacy.open_request_not_found'))
        return {'ok':True}
    return router
