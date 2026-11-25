"""Email second factor for ALL personal admin login paths. Fail closed on delivery failure."""
from __future__ import annotations

from reel.i18n import t as _t
import hashlib,hmac,json,re,secrets,time
from contextlib import closing
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from reel import brand_mail
from reel.web import accounts as a
from reel.audit import audit_connection

TTL=600
COOLDOWN=60
DEVICE_TTL=30*86400
COOKIE='reel_trusted_device'
ERROR_KEY='two_factor.this_code_is_invalid_or_has_expired'


def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS admin_challenges(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),session_version INTEGER NOT NULL,code_hash TEXT NOT NULL,expires REAL NOT NULL,sent_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE INDEX IF NOT EXISTS admin_challenges_user ON admin_challenges(user_id,sent_at)')
    conn.execute('CREATE TABLE IF NOT EXISTS trusted_devices(digest TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),session_version INTEGER NOT NULL,expires REAL NOT NULL,agent TEXT NOT NULL)')


def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def code_hash(key,code,env):return hmac.new(env['SECRETS']['session_key'].encode(),(key+'\0'+code).encode(),hashlib.sha256).hexdigest()
def agent(request):return digest(request.headers.get('user-agent','')[:1000])


def valid_user(row):
    return row and row['role']=='admin' and row['active'] and not row['removed_at'] and not row['deleted_at']


def trusted(request,row,conn):
    raw=request.cookies.get(COOKIE,'')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',raw):return False
    device=conn.execute('SELECT * FROM trusted_devices WHERE digest=?',(digest(raw),)).fetchone()
    return bool(device and device['user_id']==row['id'] and device['session_version']==row['session_version'] and device['expires']>time.time() and device['agent']==agent(request))


async def send(root,address,code):
    return await run_in_threadpool(brand_mail.send,root,address,_t('two_factor.reel_sign_in_verification_code'),eyebrow=_t('two_factor.sign_in_security'),title=_t('two_factor.confirm_your_sign_in'),
          paragraphs=[_t('two_factor.enter_the_code_below_to_sign_in')],code=code,code_label=_t('two_factor.your_6_digit_code'),
          note=_t('two_factor.this_code_is_valid_for_10_minutes'))


def finish(request,row,d):
    row=a.session_user(request,row,d)
    return {'role':row['role'],'user':a.public_user(row)}


async def login(request,row,d,env):
    ensure(d.conn)
    if not valid_user(row):raise HTTPException(401,_t(a.AUTH_ERROR_KEY))
    if trusted(request,row,d.conn):
        audit_connection(d.conn,row['id'],'auth.remembered','user:'+str(row['id']),{'status':'success'})
        return finish(request,row,d)
    a.login_limit(d,'second-factor:'+str(row['id']),'account')
    now=time.time();raw=secrets.token_urlsafe(32)
    d.conn.execute('DELETE FROM admin_challenges WHERE expires<?',(now-86400,))
    d.conn.execute('DELETE FROM trusted_devices WHERE expires<?',(now,))
    key=digest(raw);code=f'{secrets.randbelow(1_000_000):06d}'
    d.conn.execute('BEGIN IMMEDIATE')
    try:
        recent=d.conn.execute('SELECT sent_at FROM admin_challenges WHERE user_id=? ORDER BY sent_at DESC LIMIT 1',(row['id'],)).fetchone()
        if recent and now-recent[0]<COOLDOWN:raise HTTPException(429,_t('two_factor.you_can_request_a_new_code_after'))
        d.conn.execute('UPDATE admin_challenges SET used=1 WHERE user_id=?',(row['id'],))
        d.conn.execute('INSERT INTO admin_challenges(id,user_id,session_version,code_hash,expires,sent_at) VALUES(?,?,?,?,?,?)',
                       (key,row['id'],row['session_version'],code_hash(key,code,env),now+TTL,now))
        d.conn.execute('COMMIT')
    except BaseException:d.conn.execute('ROLLBACK');raise
    request.session.clear();request.session['admin_challenge']=raw
    if not await send(env['ROOT'],row['email'],code):
        d.conn.execute('UPDATE admin_challenges SET used=1 WHERE id=?',(key,));request.session.clear()
        raise HTTPException(503,_t('two_factor.the_sign_in_code_email_could_not'))
    audit_connection(d.conn,row['id'],'auth.challenge','user:'+str(row['id']),{'status':'sent'})
    return {'second_factor':True,'expires_in':TTL,'resend_after':COOLDOWN}


def challenge(request,conn):
    raw=request.session.get('admin_challenge','')
    if not isinstance(raw,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',raw):raise HTTPException(401,_t(ERROR_KEY))
    row=conn.execute('SELECT * FROM admin_challenges WHERE id=?',(digest(raw),)).fetchone()
    if not row or row['used'] or row['expires']<=time.time() or row['attempts']>=5:raise HTTPException(401,_t(ERROR_KEY))
    user=conn.execute('SELECT * FROM users WHERE id=?',(row['user_id'],)).fetchone()
    if not valid_user(user) or user['session_version']!=row['session_version']:raise HTTPException(401,_t(ERROR_KEY))
    return row,user


def make_router(env):
    router=APIRouter(prefix='/api/admin-auth')
    async def body(request):
        data=bytearray()
        async for piece in request.stream():
            data.extend(piece)
            if len(data)>2048:raise HTTPException(413,_t('account_api.request_too_large'))
        try:
            result=json.loads(data or b'{}')
            if not isinstance(result,dict):raise ValueError()
            return result
        except (ValueError,UnicodeError):raise HTTPException(400,_t('access.invalid_request')) from None
    @router.get('/status')
    def status(request:Request):
        with closing(env['db']().conn) as conn:
            ensure(conn)
            try:
                row,_=challenge(request,conn)
                return {'second_factor':True,'resend_after':max(0,int(COOLDOWN-(time.time()-row['sent_at'])))}
            except HTTPException:return {'second_factor':False}
    @router.post('/verify')
    async def verify(request:Request):
        data=await body(request);d=env['db']()
        try:
            ensure(d.conn);d.conn.execute('BEGIN IMMEDIATE')
            try:
                row,user=challenge(request,d.conn)
                a.login_limit(d,'second-factor:'+str(user['id']),'account')
                code=data.get('code');valid=isinstance(code,str) and bool(re.fullmatch(r'\d{6}',code)) and hmac.compare_digest(code_hash(row['id'],code,env),row['code_hash'])
                if valid:d.conn.execute('UPDATE admin_challenges SET used=1 WHERE id=?',(row['id'],))
                else:d.conn.execute('UPDATE admin_challenges SET attempts=attempts+1 WHERE id=?',(row['id'],))
                d.conn.execute('COMMIT')
            except BaseException:
                d.conn.execute('ROLLBACK');raise
            audit_connection(d.conn,user['id'],'auth.verified' if valid else 'auth.failed','user:'+str(user['id']),{'status':'success' if valid else 'failed'})
            if not valid:
                a.login_limit(d,'second-factor:'+str(user['id']),'account',failed=True)
                raise HTTPException(401,_t(ERROR_KEY))
            a.login_limit(d,'second-factor:'+str(user['id']),'account',failed=False)
            response=JSONResponse(finish(request,user,d))
            if data.get('remember') is True:
                raw=secrets.token_urlsafe(32)
                d.conn.execute('INSERT INTO trusted_devices VALUES(?,?,?,?,?)',(digest(raw),user['id'],user['session_version'],time.time()+DEVICE_TTL,agent(request)))
                response.set_cookie(COOKIE,raw,max_age=DEVICE_TTL,httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/api')
            return response
        finally:d.conn.close()
    @router.post('/resend')
    async def resend(request:Request):
        await body(request);d=env['db']()
        try:
            ensure(d.conn);d.conn.execute('BEGIN IMMEDIATE')
            try:
                row,user=challenge(request,d.conn);a.login_limit(d,'second-factor:'+str(user['id']),'account')
                if time.time()-row['sent_at']<COOLDOWN:raise HTTPException(429,_t('two_factor.wait_60_seconds_before_resending'))
                code=f'{secrets.randbelow(1_000_000):06d}';now=time.time()
                if hmac.compare_digest(code_hash(row['id'],code,env),row['code_hash']):code=f'{(int(code)+1)%1_000_000:06d}'
                d.conn.execute('UPDATE admin_challenges SET code_hash=?,sent_at=?,expires=? WHERE id=?',(code_hash(row['id'],code,env),now,now+TTL,row['id']))
                d.conn.execute('COMMIT')
            except BaseException:d.conn.execute('ROLLBACK');raise
            if not await send(env['ROOT'],user['email'],code):raise HTTPException(503,_t('two_factor.the_email_could_not_be_sent_try'))
            audit_connection(d.conn,user['id'],'auth.resend','user:'+str(user['id']),{'status':'sent'})
            return {'second_factor':True,'expires_in':TTL,'resend_after':COOLDOWN}
        finally:d.conn.close()
    @router.post('/forget-devices')
    def forget(request:Request):
        p=getattr(request.state,'principal',None)
        if not p or p.get('role')!='admin' or not p.get('id'):raise HTTPException(403,_t('two_factor.personal_administrator_accounts_only'))
        with closing(env['db']().conn) as conn:
            ensure(conn);conn.execute('DELETE FROM trusted_devices WHERE user_id=?',(p['id'],))
            audit_connection(conn,p['id'],'auth.devices_removed','user:'+str(p['id']),{})
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/api');return response
    return router
