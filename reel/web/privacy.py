"""Versioned recording consent and operator-reviewed data requests. No automatic media deletion."""
from __future__ import annotations
import json,re,time
from contextlib import closing
from fastapi import APIRouter,HTTPException,Request

VERSION='2026-11-20'
NOTICE='Դասերը ձայնագրվում են, դեմքերը մշուշվում են'
POLICY='''Reel-ը դասերի տեսագրությունների մշակման վահանակն է։
Դասերը ձայնագրվում են։ Համակարգը մշակում է տեսանյութը, ձայնը, խոսքի տեքստը, դասի տվյալները և հաշվի տվյալները։
Վստահ ճանաչված ուսանողի դեմքը մշուշվում է․ անորոշ դեպքերում դեմքը կարող է տեսանելի մնալ։ Դեմքերի մշուշումը անսխալական չէ։
Չմշուշված սկզբնաղբյուրը վեբով չի ցուցադրվում։ Տեսանյութը կարող է հրապարակվել YouTube-ում չցուցակագրված հղումով, իսկ հղումը՝ խմբի Slack-ում։ Հղումը ստացողը կարող է այն փոխանցել ուրիշներին։
Ձեր խմբի գրադարանին հասանելիություն ունեն այդ խմբի իրավասու օգտատերերը և ադմինները։ Ծրագրի համարները և ախտորոշումները հասանելի են օպերատորներին։
Հաշվի տվյալները, սկզբնական նյութերը և մշակման արդյունքները պահվում են կազմակերպության համակարգում՝ ըստ գործող մաքրման կարգավորումների։ Միասնական ֆիքսված պահպանման ժամկետ այս էջը չի խոստանում։
Կարող եք դիմել տվյալների հասանելիության, ճշտման կամ ջնջման համար՝ ձեր պրոֆիլից կամ ակադեմիայի ադմինին։ Հարցումը ստուգվում է ադմինի կողմից, նաև արտաքին հրապարակումների և այլ անձանց իրավունքների համատեքստում։ Ավտոմատ ջնջում կամ որոշակի օրերի ընթացքում ավարտ այս համակարգը չի խոստանում։
AI Junior-ի ուսանողի գրանցման համաձայնությունը տալիս է ծնողը կամ օրինական ներկայացուցիչը։ Այս գրանցումը պահպանում է ներկայացուցչի հայտարարությունը․ ինքնուրույն չի հաստատում նրա ինքնությունը կամ իրավական կարգավիճակը։
Հարցերի կամ համաձայնությունը հետ վերցնելու համար դիմեք ադմինին։'''


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
    if data.get('recording_consent') is not True:raise HTTPException(400,'Հաստատեք ձայնագրման և գաղտնիության պայմանները։')
    name=email=None
    if junior(payload,conn):
        name=str(data.get('guardian_name') or '').strip();email=str(data.get('guardian_email') or '').strip()
        if data.get('guardian_consent') is not True or not 2<=len(name)<=120 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:
            raise HTTPException(400,'AI Junior-ի համար անհրաժեշտ է ծնողի/օրինական ներկայացուցչի համաձայնությունը, անունը և էլ․ հասցեն։')
    return dict(version=VERSION,guardian_name=name,guardian_email=email)


def record(conn,uid,consent):
    conn.execute('INSERT INTO recording_consents VALUES(?,?,?,?,?)',(uid,consent['version'],time.time(),consent['guardian_name'],consent['guardian_email']))
    from reel.audit import audit_connection
    audit_connection(conn,uid,'privacy.consent','user:'+str(uid),{'status':'accepted','reason':'guardian' if consent['guardian_name'] else 'recording'})


def make_router(env):
    router=APIRouter(prefix='/api/privacy')
    @router.get('/policy')
    def policy():return dict(version=VERSION,notice=NOTICE,paragraphs=POLICY.splitlines())
    def person(request):
        p=getattr(request.state,'principal',None)
        if not p or not p.get('id'):raise HTTPException(401,'Մուտք գործեք անձնական հաշվով։')
        return p
    @router.post('/requests')
    async def request_deletion(request:Request):
        p=person(request)
        if int(request.headers.get('content-length') or 0)>4096:raise HTTPException(413,'Հարցումը չափազանց մեծ է։')
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:raise HTTPException(413,'Հարցումը չափազանց մեծ է։')
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
        except (ValueError,UnicodeError):raise HTTPException(400,'Անվավեր հարցում։') from None
        message=str(data.get('message') or '')
        if not 5<=len(message)<=2000:raise HTTPException(400,'Նկարագրեք խնդրանքը՝ 5–2000 նիշով։')
        with closing(env['db']().conn) as conn:
            ensure(conn);conn.execute('BEGIN IMMEDIATE')
            try:
                row=conn.execute("SELECT id FROM data_requests WHERE user_id=? AND status='open'",(p['id'],)).fetchone()
                ident=row[0] if row else conn.execute('INSERT INTO data_requests(user_id,created_at,message) VALUES(?,?,?)',(p['id'],time.time(),message)).lastrowid
                conn.execute('COMMIT')
            except BaseException:conn.execute('ROLLBACK');raise
        return {'id':ident,'message':'Հարցումը պահպանված է։ Ադմինը կվերանայի այն։'}
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
        if p['role']!='admin':raise HTTPException(403,'Միայն ադմինին։')
        with closing(env['db']().conn) as conn:
            ensure(conn);cur=conn.execute("UPDATE data_requests SET status='resolved',resolved_at=?,resolved_by=? WHERE id=? AND status='open'",(time.time(),p['id'],ident))
        if not cur.rowcount:raise HTTPException(404,'Բաց հարցումը չի գտնվել։')
        return {'ok':True}
    return router
