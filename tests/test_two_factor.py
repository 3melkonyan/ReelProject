import time,asyncio
import pytest
from reel.db import DB
from reel.web import two_factor as tf
from test_web_upload import web


@pytest.fixture
def secured(web,monkeypatch):
    codes=[]
    async def fake(root,email,code):codes.append((email,code));return True
    monkeypatch.setattr(tf,'send',fake)
    web.module._admin_login=tf.login
    return web,codes


def begin(web):
    c=web.client();status,result=c.call('POST','/api/login',body={'login':'fixture-admin','password':'up-admin-pw'})
    assert status==200 and result['second_factor'] and 'role' not in result
    return c


def test_password_is_not_admin_session_code_is_hashed_single_use(secured):
    web,codes=secured;c=begin(web)
    assert c.call('GET','/api/accounts/users')[0]==401
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]},csrf=False)[0]==400
    d=DB(web.root/'reel.db');row=d.conn.execute('SELECT * FROM admin_challenges').fetchone()
    assert len(row['code_hash'])==64 and codes[-1][1] not in str(tuple(row))
    d.conn.close()
    status,r=c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]})
    assert status==200 and r['role']=='admin'
    assert c.call('GET','/api/accounts/users')[0]==200
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]})[0]==401


def test_five_attempts_expiry_resend_cooldown(secured):
    web,codes=secured;c=begin(web)
    assert c.call('POST','/api/admin-auth/resend')[0]==429
    correct=codes[-1][1];wrong='000001' if correct!='000001' else '000002'
    for _ in range(5):assert c.call('POST','/api/admin-auth/verify',body={'code':wrong})[0]==401
    assert c.call('POST','/api/admin-auth/verify',body={'code':correct})[0] in (401,429)
    assert c.call('POST','/api/admin-auth/resend')[0] in (401,429)
    assert c.call('GET','/api/accounts/users')[0]==401


def test_resend_invalidates_previous_and_preserves_attempts(secured):
    web,codes=secured;c=begin(web);old=codes[-1][1]
    d=DB(web.root/'reel.db');d.conn.execute('UPDATE admin_challenges SET sent_at=sent_at-61,attempts=1');d.conn.close()
    assert c.call('POST','/api/admin-auth/resend')[0]==200
    d=DB(web.root/'reel.db');assert d.conn.execute('SELECT attempts FROM admin_challenges').fetchone()[0]==1
    d.conn.execute('UPDATE admin_challenges SET expires=?',(time.time()-1,));d.conn.close()
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]})[0]==401


def test_delivery_failure_never_authenticates(secured,monkeypatch):
    web,codes=secured
    async def fail(*args):return False
    monkeypatch.setattr(tf,'send',fail)
    c=web.client();assert c.call('POST','/api/login',body={'login':'fixture-admin','password':'up-admin-pw'})[0]==503
    assert c.call('GET','/api/accounts/users')[0]==401


def test_remembered_device_still_needs_password_and_revokes(secured):
    web,codes=secured;c=begin(web)
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1],'remember':True})[0]==200
    d=DB(web.root/'reel.db');row=d.conn.execute('SELECT * FROM trusted_devices').fetchone();assert len(row['digest'])==64;d.conn.close()
    assert c.call('POST','/api/logout')[0]==200
    assert c.call('POST','/api/login',body={'login':'fixture-admin','password':'wrong'})[0]==401
    assert c.call('POST','/api/login',body={'login':'fixture-admin','password':'up-admin-pw'})[1]['role']=='admin'
    assert len(codes)==1
    assert c.call('POST','/api/admin-auth/forget-devices')[0]==200
    d=DB(web.root/'reel.db');assert d.conn.execute('SELECT count(*) FROM trusted_devices').fetchone()[0]==0
    d.conn.execute('UPDATE admin_challenges SET sent_at=sent_at-61');d.conn.close()
    c.call('POST','/api/logout')
    assert c.call('POST','/api/login',body={'login':'fixture-admin','password':'up-admin-pw'})[1]['second_factor']


def test_account_version_change_invalidates_pending_and_devices(secured):
    web,codes=secured;c=begin(web)
    d=DB(web.root/'reel.db');d.conn.execute('UPDATE users SET session_version=session_version+1');d.conn.close()
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]})[0]==401


def test_bootstrap_also_requires_code_and_consent(secured):
    web,codes=secured
    d=DB(web.root/'reel.db');d.conn.execute('DELETE FROM users');d.conn.close()
    c=web.client();assert c.call('POST','/api/login',body={'password':'up-admin-pw'})[1]['bootstrap']
    data=dict(name='Ադմին',email='admin@example.test',login='new-owner',password='strong-password-123')
    assert c.call('POST','/api/accounts/bootstrap',body=data)[0]==400
    assert c.call('POST','/api/accounts/bootstrap',body={**data,'recording_consent':True})[1]['second_factor']
    assert c.call('GET','/api/accounts/users')[0]==401
    assert c.call('POST','/api/admin-auth/verify',body={'code':codes[-1][1]})[1]['role']=='admin'


def test_parallel_verification_has_exactly_one_winner(secured):
    from concurrent.futures import ThreadPoolExecutor
    from urllib.request import Request,build_opener,ProxyHandler
    from urllib.error import HTTPError
    import json
    web,codes=secured;c=begin(web)
    jar=next(h.cookiejar for h in c.opener.handlers if hasattr(h,'cookiejar'))
    cookie='; '.join(x.name+'='+x.value for x in jar)
    def verify(_):
        req=Request(c.base+'/api/admin-auth/verify',data=json.dumps({'code':codes[-1][1]}).encode(),method='POST',
                    headers={'Content-Type':'application/json','X-Requested-With':'reel','Cookie':cookie})
        try:
            with build_opener(ProxyHandler({})).open(req) as r:return r.status
        except HTTPError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(verify,range(2)))
    assert sorted(results)==[200,401]


def test_pending_status_survives_refresh_without_granting_role(secured):
    web,codes=secured;c=begin(web)
    assert c.call('GET','/api/admin-auth/status')[1]['second_factor']
    assert c.call('GET','/api/me')[0]==401
    assert not web.client().call('GET','/api/admin-auth/status')[1]['second_factor']
