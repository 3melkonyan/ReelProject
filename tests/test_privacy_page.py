
import pytest
from fastapi import HTTPException
from reel.db import DB
from reel.i18n import t
from reel.web import privacy

from test_accounts import invite,PASSWORD


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from reel.web import account_mail
    monkeypatch.setattr(account_mail,'validate_email',lambda _:[])
    monkeypatch.setattr(account_mail,'send_invitation',lambda *a,**k:False)


def test_no_account_or_token_consumption_without_consent(web):
    token,ident,_=invite(web);client=web.client()
    data=dict(token=token,login='consenting',password=PASSWORD)
    assert client.call('POST','/api/accounts/register',body=data)[0]==400
    d=DB(web.root/'reel.db')
    assert d.conn.execute('SELECT used_at FROM account_tokens WHERE id=?',(ident,)).fetchone()[0] is None
    d.conn.close()
    code,r=client.call('POST','/api/accounts/register',body={**data,'recording_consent':True})
    assert code==200
    d=DB(web.root/'reel.db');row=d.conn.execute('SELECT * FROM recording_consents WHERE user_id=?',(r['user']['id'],)).fetchone()
    assert row['policy_version']==privacy.VERSION;d.conn.close()
    assert client.call('POST','/api/privacy/requests',body={'message':'Խնդրում եմ ջնջել իմ տվյալները։'},csrf=False)[0]==400
    one=client.call('POST','/api/privacy/requests',body={'message':'Խնդրում եմ ջնջել իմ տվյալները։'})
    two=client.call('POST','/api/privacy/requests',body={'message':'Կրկնվող խնդրանք։'})
    assert one[1]['id']==two[1]['id']
    assert client.call('POST',f"/api/privacy/requests/{one[1]['id']}/resolve")[0]==403
    admin=web.client('admin')
    assert len(admin.call('GET','/api/privacy/requests')[1])==1
    assert admin.call('POST',f"/api/privacy/requests/{one[1]['id']}/resolve")[0]==200
    assert client.call('GET','/api/privacy/requests')[1][0]['status']=='resolved'


def test_junior_guardian_requirement_cannot_be_spoofed():
    payload={'role':'student','groups':['AI Junior 2.6']}
    assert privacy.junior(payload)
    with pytest.raises(HTTPException):privacy.validate({'recording_consent':True,'guardian_required':False},payload)
    c=privacy.validate(dict(recording_consent=True,guardian_consent=True,guardian_name='Ծնողի անուն',guardian_email='parent@example.test'),payload)
    assert c['guardian_email']=='parent@example.test'
    assert not privacy.junior({'role':'lecturer','groups':['AI Junior']})


def test_policy_public_but_requests_personal(web):
    c=web.client();assert c.call('GET','/api/privacy/policy')[1]['notice']==t(privacy.NOTICE_KEY, language='hy')
    assert c.call('GET','/api/privacy/requests')[0]==401
    assert web.client('viewer').call('GET','/api/privacy/requests')[0]==401
