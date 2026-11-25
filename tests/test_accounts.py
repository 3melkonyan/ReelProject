"""G-36: temporary account databases, HTTP group matrix, fake SMTP only."""
from concurrent.futures import ThreadPoolExecutor
import base64,io,json,logging,secrets,sqlite3,struct,sys,time,zlib
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from reel.db import DB
from reel.web import accounts as a
from test_web_upload import web

PASSWORD='Անձնական-password-123'


@pytest.fixture(autouse=True)
def offline_g39_mail(monkeypatch):
    from reel.web import account_mail
    monkeypatch.setattr(account_mail, 'validate_email', lambda email: [])

    def no_real_smtp(*args, **kwargs):
        raise AssertionError('G36 regressions must use fake SMTP')

    monkeypatch.setattr(account_mail.smtplib, 'SMTP', no_real_smtp)


def invite(web,role='student',groups=None,index=1):
    admin=web.client('admin');payload={'name':f'Օգտատեր {index}','email':f'user{index}@example.test','role':role,
                                     'groups':groups if groups is not None else ['Group-A']}
    code,result=admin.call('POST','/api/accounts/invitations',body=payload)
    assert code==200,result
    return result['link'].rsplit('/',1)[1],result['id'],admin


def register(web,token,login='user-one'):
    c=web.client();code,result=c.call('POST','/api/accounts/register',body={'recording_consent':True,'token':token,'login':login,'password':PASSWORD})
    assert code==200,result
    return c,result['user']


def seed_jobs(web):
    db=DB(web.root/'reel.db');ids=[]
    for group,vid in [('Group-A','abcdefghijk'),('Group-B','lmnopqrstuv')]:
        jid=db.create_job('source.mp4','job-'+group,group,'Python');ids.append(jid)
        for name,out in [('probe',{'duration_sec':1000}),('segments',{'keep':[[0,900]],'cuts':[{'start':900,'end':1000,'why':'after_class'}],
                         'original_sec':1000,'final_sec':900}),('metadata',{'title':group,'chapters':[]} ),('privacy',{})]:
            db.stage_start(jid,name);db.stage_done(jid,name,out)
        db.add_publication(jid,'youtube',vid,'https://youtu.be/'+vid)
        db.set_job(jid,status='done')
        folder=web.root/'work'/str(jid);folder.mkdir(parents=True)
        (folder/'thumbnail.jpg').write_bytes(b'thumbnail');(folder/'final_blurred.mp4').write_bytes(b'blurred')
    db.conn.close();return ids


def test_bootstrap_only_and_shared_switch(web):
    db=DB(web.root/'reel.db');db.conn.execute('DELETE FROM users');db.conn.close()
    client=web.client();assert client.call('POST','/api/login',body={'password':'up-admin-pw'})==(200,{'role':'bootstrap','bootstrap':True})
    assert client.call('GET','/api/jobs')[0]==403
    data={'recording_consent':True,'name':'Առաջին ադմին','email':'owner@example.test','login':'Owner','password':PASSWORD}
    assert client.call('POST','/api/accounts/bootstrap',body=data,csrf=False)[0]==400
    assert client.call('POST','/api/accounts/bootstrap',body=data)[1]['user']['login']=='owner'
    assert client.call('POST','/api/accounts/bootstrap',body=data)[0]==403
    assert client.call('GET','/api/accounts/users')[0]==200
    shared=web.client('viewer');assert shared.call('GET','/api/jobs')[0]==200
    assert client.call('POST','/api/accounts/legacy',body={'enabled':False})[0]==200
    assert shared.call('GET','/api/jobs')[0]==401
    assert web.client().call('POST','/api/login',body={'password':'up-viewer-pw'})[0]==401
    assert client.call('GET','/api/accounts/users')[0]==200


def test_hashes_tokens_immutable_invitation_and_unique_login(web,caplog):
    token,ident,admin=invite(web)
    public=web.client().call('POST','/api/accounts/invitation',body={'token':token})[1]
    assert public['role']=='student' and public['groups']==['Group-A']
    c=web.client();code,result=c.call('POST','/api/accounts/register',body={'recording_consent':True,'token':token,'login':'CASE-User','password':PASSWORD,'role':'admin','groups':['Group-B']})
    assert code==200 and result['user']['last_login_at'] and result['user']['role']=='student' and result['user']['groups']==['Group-A']
    db=DB(web.root/'reel.db');row=db.conn.execute('SELECT * FROM users WHERE id=?',(result['user']['id'],)).fetchone()
    assert row['login']=='case-user' and row['password_hash'].startswith('scrypt$') and PASSWORD not in row['password_hash']
    assert a.verify_password(PASSWORD,row['password_hash'])
    raw='\n'.join(str(tuple(r)) for r in db.conn.execute('SELECT * FROM account_tokens'))
    assert token not in raw and a.hashlib.sha256(token.encode()).hexdigest() in raw
    db.conn.close()
    assert c.call('POST','/api/accounts/register',body={'recording_consent':True,'token':token,'login':'other','password':PASSWORD})[0]==400
    login=web.client();assert login.call('POST','/api/login',body={'login':'CASE-USER','password':PASSWORD})[0]==200
    email=web.client();assert email.call('POST','/api/login',body={'login':'USER1@EXAMPLE.TEST','password':PASSWORD})[0]==200
    assert token not in caplog.text and PASSWORD not in caplog.text
    item=admin.call('GET','/api/accounts/invitations')[1][0]
    assert item['status']=='used' and item['registered_login']=='case-user'


def test_concurrent_invitation_redeem_and_expired_revoked(web):
    token,_,_=invite(web)
    def redeem(login):return web.client().call('POST','/api/accounts/register',body={'recording_consent':True,'token':token,'login':login,'password':PASSWORD})[0]
    with ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(redeem,['concurrent-one','concurrent-two']))
    assert sorted(codes)==[200,400]
    for index,state in [(2,'expired'),(3,'revoked')]:
        token,ident,admin=invite(web,index=index)
        if state=='expired':
            db=DB(web.root/'reel.db');db.conn.execute('UPDATE account_tokens SET expires_at=? WHERE id=?',(time.time()-1,ident));db.conn.close()
        else:assert admin.call('POST',f'/api/accounts/invitations/{ident}/revoke')[0]==200
        assert web.client().call('POST','/api/accounts/register',body={'recording_consent':True,'token':token,'login':'cannot-register','password':PASSWORD})[0]==400


@pytest.mark.parametrize('role',['admin','lecturer','student'])
def test_role_endpoint_group_matrix(web,role):
    jobs=seed_jobs(web);token,_,_=invite(web,role,[] if role=='admin' else ['Group-A'])
    c,user=register(web,token)
    get_routes=['','/cuts','/video','/thumbnail','/cuts/0/preview','/faces/face_1.jpg','/lecturer']
    for jid in jobs:
        for suffix in get_routes:
            code,_=c.call('GET',f'/api/jobs/{jid}'+suffix)
            if role!='admin' and jid==jobs[1]:assert code==404,(role,suffix,code)
            elif role=='student':assert code in (401,403),(suffix,code)
            elif suffix in ('','/cuts','/video','/thumbnail'):assert code==200,(role,suffix,code)
    library=c.call('GET','/api/library')[1]
    assert {r['group'] for r in library}==({'Group-A','Group-B'} if role=='admin' else {'Group-A'})
    for path in ['/api/jobs','/api/activity','/api/summary','/api/groups','/api/stats']:
        code,data=c.call('GET',path)
        if role=='student':assert code==403,(path,code)
        else:
            assert code==200,(path,code,data)
            if role=='lecturer':assert 'Group-B' not in json.dumps(data),path
    for group in ['Group-A','Group-B']:
        code,_=c.call('GET',f'/api/uploads/next-number?group={group}&subject='+('Group-A' if group=='Group-A' else 'Python'))
        if role!='admin' and group=='Group-B':assert code==404
        elif role=='student':assert code==403
        else:assert code==200
        code,_=c.call('POST','/api/uploads',body={'group':group,'subject':'Group-A' if group=='Group-A' else 'Python','lesson_number':1,'files':[{'name':'a.mp4','size':8}]})
        if role=='student':assert code==403
        elif role=='lecturer' and group=='Group-B':assert code==404
        else:assert code==200
    assert c.call('GET','/api/accounts/users')[0]==(200 if role=='admin' else 403)


def test_reset_invalidates_all_sessions_and_password_change(web):
    token,_,admin=invite(web);c,user=register(web,token)
    other=web.client();assert other.call('POST','/api/login',body={'login':'user-one','password':PASSWORD})[0]==200
    result=admin.call('POST',f"/api/accounts/users/{user['id']}/reset")[1];raw=result['link'].rsplit('/',1)[1]
    assert web.client().call('POST','/api/accounts/reset',body={'token':raw,'password':'replacement-123'})[0]==200
    assert c.call('GET','/api/me')[0]==401 and other.call('GET','/api/me')[0]==401
    assert web.client().call('POST','/api/accounts/reset',body={'token':raw,'password':'replacement-456'})[0]==400
    fresh=web.client();assert fresh.call('POST','/api/login',body={'login':'user-one','password':'replacement-123'})[0]==200
    assert fresh.call('POST','/api/accounts/password',body={'current_password':'wrong','password':'another-12345'})[0]==400
    assert fresh.call('POST','/api/accounts/password',body={'current_password':'replacement-123','password':'another-12345'})[0]==200
    assert fresh.call('GET','/api/me')[0]==401


def test_disable_role_change_last_admin_and_rate_limit(web):
    token,_,admin=invite(web,'lecturer');c,user=register(web,token)
    assert admin.call('POST',f"/api/accounts/users/{user['id']}",body={'active':False})[0]==200
    assert c.call('GET','/api/me')[0]==401
    assert admin.call('POST','/api/accounts/users/1',body={'active':False})[0]==409
    assert admin.call('POST','/api/accounts/users/1',body={'role':'student','groups':['Group-A']})[0]==409
    bad=web.client()
    for _ in range(5):assert bad.call('POST','/api/login',body={'login':'missing','password':'anything'})[0]==401
    assert bad.call('POST','/api/login',body={'login':'missing','password':'anything'})[0]==429
    assert bad.call('POST','/api/login',body={'login':'another-missing','password':'anything'})[0]==401


def test_fake_smtp_constant_response_and_trusted_url(web,monkeypatch,caplog):
    token,_,_=invite(web);c,user=register(web,token);messages=[];sent=threading.Event()
    class SMTP:
        def __init__(self,host,port,timeout):assert host=='smtp.example.test' and port==587
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ehlo(self):pass
        def starttls(self,context):pass
        def login(self,user,password):pass
        def send_message(self,msg):messages.append(msg);sent.set()
    import smtplib
    monkeypatch.setattr(smtplib,'SMTP',SMTP)
    for key,value in {'REEL_PUBLIC_URL':'https://academy.example.test','SMTP_HOST':'smtp.example.test','SMTP_USER':'fixture','SMTP_PASSWORD':'smtp-test-secret','SMTP_FROM':'academy@example.test'}.items():monkeypatch.setenv(key,value)
    known=c.call('POST','/api/accounts/forgot',body={'login':'user-one'});unknown=c.call('POST','/api/accounts/forgot',body={'login':'nobody'})
    assert sent.wait(2)
    assert known==unknown and len(messages)==1
    body=messages[0].get_body(preferencelist=('plain',)).get_content();assert 'https://academy.example.test/#/reset/' in body
    assert 'smtp-test-secret' not in caplog.text
    raw=body.split('/#/reset/')[1].split()[0];assert raw not in caplog.text
    monkeypatch.delenv('REEL_PUBLIC_URL');monkeypatch.delenv('SMTP_HOST')
    assert c.call('POST','/api/accounts/forgot',body={'login':'user-one'})==known


@pytest.mark.parametrize('path', ['/api/login','/api/logout','/api/accounts/forgot','/api/accounts/register','/api/accounts/reset','/api/accounts/bootstrap','/api/accounts/invitation','/api/accounts/profile','/api/accounts/password','/api/accounts/avatar','/api/accounts/invitations','/api/accounts/legacy','/api/accounts/users/1','/api/accounts/users/1/reset','/api/accounts/invitations/1/revoke'])
def test_all_mutations_require_csrf(web,path):assert web.client().call('POST',path,body={},csrf=False)[0]==400


def test_avatar_validation_reencoding_and_authenticated_route(web):
    token,_,_=invite(web);c,user=register(web,token)
    assert c.call('POST','/api/accounts/avatar',raw=b'not an image')[0]==400
    assert c.call('POST','/api/accounts/avatar',raw=b'x'*(5*1024*1024+1))[0]==413
    def png_chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    giant=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',8001,8001,8,2,0,0,0))+png_chunk(b'IEND',b'')
    assert c.call('POST','/api/accounts/avatar',raw=giant)[0]==400
    image=Image.new('RGB',(400,300),(255,0,100));buffer=io.BytesIO();exif=Image.Exif();exif[270]='private-location-marker';image.save(buffer,format='PNG',exif=exif)
    assert Image.open(io.BytesIO(buffer.getvalue())).getexif()[270]=='private-location-marker'
    assert c.call('POST','/api/accounts/avatar',raw=buffer.getvalue())[0]==200
    status,raw=c.call('GET',f"/api/accounts/avatars/{user['id']}");assert status==200
    assert b'private-location-marker' not in raw
    with Image.open(io.BytesIO(raw)) as photo:assert photo.size==(256,256) and photo.format=='JPEG' and not photo.getexif()
    assert web.client().call('GET',f"/api/accounts/avatars/{user['id']}")[0]==401
    assert c.call('POST','/api/accounts/profile',body={'name':'Փոխված անուն'})[0]==200
    assert c.call('GET','/api/accounts/profile')[1]['name']=='Փոխված անուն'


def test_upload_resume_receipts_group_scope_and_group_change(web):
    jobs=seed_jobs(web);admin=web.client('admin')
    code,upload=admin.call('POST','/api/uploads',body={'group':'Group-B','subject':'Python','lesson_number':8,'files':[{'name':'part.mp4','size':8}]})
    assert code==200;uid=upload['id']
    token,_,_=invite(web,'lecturer',['Group-A']);c,user=register(web,token)
    for method,path in [('GET',f'/api/uploads/{uid}'),('PUT',f'/api/uploads/{uid}/0?offset=0'),('POST',f'/api/uploads/{uid}/finish'),('DELETE',f'/api/uploads/{uid}')]:
        assert c.call(method,path)[0]==404,(method,path)
    assert admin.call('POST',f"/api/accounts/users/{user['id']}",body={'groups':['Group-B']})[0]==200
    assert c.call('GET','/api/me')[0]==401
    assert c.call('POST','/api/login',body={'login':'user-one','password':PASSWORD})[0]==200
    assert c.call('GET',f'/api/jobs/{jobs[0]}')[0]==404
    assert c.call('GET',f'/api/jobs/{jobs[1]}')[0]==200
    assert c.call('GET',f'/api/uploads/{uid}')[0]==200
    folder=web.root/'out'/'uploads'/'receipts'
    # Use the app's actual uploads root, not a guessed path.
    module=next(m for m in list(sys.modules.values()) if getattr(m,'__name__','').startswith('_reel_upload_web_') and getattr(m,'ROOT',None)==web.root)
    folder=module._uploads()/'receipts';folder.mkdir(exist_ok=True)
    (folder/(uid+'.json')).write_text(json.dumps({'duplicate_of':jobs[0]}))
    assert c.call('GET',f'/api/uploads/{uid}')[0]==404


def test_multiple_lecturer_groups_and_invalid_assignments(web):
    seed_jobs(web);token,_,admin=invite(web,'lecturer',['Group-A','Group-B']);c,_=register(web,token)
    assert len(c.call('GET','/api/library')[1])==2
    for role,selected in [('student',[]),('student',['Group-A','Group-B']),('lecturer',[]),('lecturer',['absent'])]:
        assert admin.call('POST','/api/accounts/invitations',body={'name':'Անուն','email':'new@example.test','role':role,'groups':selected})[0]==400


def test_cookie_flags_on_actual_session_middleware():
    import asyncio
    from reel.web.access import SecureSessionMiddleware
    async def run(scheme):
        messages=[]
        async def app(scope,receive,send):
            scope['session']['role']='student'
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(message):messages.append(message)
        middleware=SecureSessionMiddleware(app,secret_key='only-a-test-secret',same_site='lax')
        await middleware({'type':'http','scheme':scheme,'headers':[],'path':'/'},receive,send)
        return dict(messages[0]['headers'])[b'set-cookie'].decode().lower()
    http=asyncio.run(run('http'));https=asyncio.run(run('https'))
    assert 'httponly' in https and 'samesite=lax' in https and '; secure' in https
    assert '; secure' not in http


def test_public_url_and_tunnel_file_not_request_host(web,monkeypatch):
    monkeypatch.delenv('REEL_PUBLIC_URL',raising=False)
    assert a.public_base(web.root) is None
    (web.root/'logs').mkdir();path=web.root/'logs'/'tunnel_url.txt'
    path.write_text('https://fixture-tunnel.trycloudflare.com');assert a.public_base(web.root)=='https://fixture-tunnel.trycloudflare.com'
    path.write_text('https://attacker.example.test');assert a.public_base(web.root) is None
    for url in ['http://remote.example.test','https://user:pass@example.test','https://[malformed','https://good.test:bad','https://good.test/?injection=1']:
        monkeypatch.setenv('REEL_PUBLIC_URL',url);assert a.public_base(web.root) is None
    monkeypatch.setenv('REEL_PUBLIC_URL','https://trusted.example.test')
    assert a.link(web.root,'reset','abc').startswith('https://trusted.example.test/')


def test_parallel_group_requests_do_not_share_principal(web):
    seed_jobs(web)
    one,_,_=invite(web,'lecturer',['Group-A'],index=11);client_a,_=register(web,one,'group-a')
    two,_,_=invite(web,'lecturer',['Group-B'],index=12);client_b,_=register(web,two,'group-b')
    def load(pair):
        client,group=pair
        for _ in range(5):
            code,data=client.call('GET','/api/jobs')
            assert code==200 and {j['group'] for j in data}=={group}
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(load,[(client_a,'Group-A'),(client_b,'Group-B')]))


def test_reset_expiration_and_no_smtp_error_secret_logs(web,monkeypatch,caplog):
    token,_,admin=invite(web);client,user=register(web,token)
    link=admin.call('POST',f"/api/accounts/users/{user['id']}/reset")[1]['link'];raw=link.rsplit('/',1)[1]
    db=DB(web.root/'reel.db');row=db.conn.execute("SELECT * FROM account_tokens WHERE kind='reset'").fetchone()
    assert row['expires_at']-row['created_at']==3600
    db.conn.execute('UPDATE account_tokens SET expires_at=? WHERE id=?',(time.time()-1,row['id']));db.conn.close()
    assert client.call('POST','/api/accounts/reset',body={'token':raw,'password':'another-password'})[0]==400
    for key,value in {'REEL_PUBLIC_URL':'https://trusted.example.test','SMTP_HOST':'fake','SMTP_USER':'fake','SMTP_PASSWORD':'secret-not-for-logs','SMTP_FROM':'test@example.test'}.items():monkeypatch.setenv(key,value)
    import smtplib
    def fail(*args,**kwargs):raise RuntimeError(raw+' secret-not-for-logs')
    monkeypatch.setattr(smtplib,'SMTP',fail)
    assert a.send_reset(web.root,'test@example.test',link) is False
    assert raw not in caplog.text and 'secret-not-for-logs' not in caplog.text
