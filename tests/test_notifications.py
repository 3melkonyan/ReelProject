from reel import notifications as n,brand_mail
from reel.db import DB
from reel.web.accounts import create_user


def fixture(tmp_path):
    d=DB(tmp_path/'test.db')
    create_user(d,'admin-one','strong-password-123',dict(name='Ադմին',email='admin@example.test',role='admin',groups=[]))
    create_user(d,'teacher','strong-password-123',dict(name='Դասախոս',email='teacher@example.test',role='lecturer',groups=['G']))
    jid=d.create_job('fake','fakehash','G');d.set_job(jid,status='running')
    return d,jid


def test_only_admins_and_dedup_survives_restart(tmp_path):
    d,jid=fixture(tmp_path);sent=[]
    def email(address, text):
        return sent.append(('email',address,text)) or True
    def slack(address, text):
        return sent.append(('slack',address,text)) or True
    for _ in range(3):n.poll(d.conn,tmp_path,alive=False,free=1,email=email,slack=slack)
    assert len(sent)==4 and {x[1] for x in sent}=={'admin@example.test'}
    d.conn.close();d=DB(tmp_path/'test.db')
    n.poll(d.conn,tmp_path,alive=False,free=1,email=email,slack=slack);assert len(sent)==4
    n.poll(d.conn,tmp_path,alive=True,free=10**12,email=email,slack=slack)
    n.poll(d.conn,tmp_path,alive=False,free=1,email=email,slack=slack);assert len(sent)==8
    d.conn.close()


def test_failed_sends_not_spammed_and_no_raw_error_leak(tmp_path):
    d,jid=fixture(tmp_path)
    d.conn.execute("INSERT INTO stages(job_id,name,status,error) VALUES(?,'youtube_upload','failed','SECRET_TOKEN')",(jid,))
    calls=[]
    def fail(*args):calls.append(args);raise RuntimeError('SECRET')
    for _ in range(2):n.poll(d.conn,tmp_path,alive=True,free=10**12,email=fail,slack=fail)
    assert len(calls)==2 and 'SECRET' not in str(calls)
    assert {r[0] for r in d.conn.execute('SELECT state FROM notification_delivery')}=={'failed'}
    d.conn.close()


def test_branded_message_code_is_escaped_and_multipart():
    m=brand_mail.build('admin@example.test','Մուտք',eyebrow='ԱՆՎՏԱՆԳՈՒԹՅՈՒՆ',title='Հաստատեք մուտքը',code='123456',code_label='Կոդ',paragraphs=['<script>'])
    assert '123456' in m.get_body(preferencelist=('plain',)).get_content()
    html=m.get_body(preferencelist=('html',)).get_content()
    assert '&lt;script&gt;' in html and 'cid:reel-logo' in html
    assert len(m.as_bytes())<100_000


def test_monitor_lifespan_starts_and_stops_without_worker(tmp_path,monkeypatch):
    import asyncio,sqlite3
    from types import SimpleNamespace
    from fastapi import FastAPI
    from reel import lesson_report
    calls=[]
    monkeypatch.setattr(n,'poll',lambda *args:calls.append('alerts'))
    monkeypatch.setattr(lesson_report,'poll',lambda *args:calls.append('reports'))
    app=FastAPI();n.install(app,{'ROOT':tmp_path,'db':lambda:SimpleNamespace(conn=sqlite3.connect(tmp_path/'temporary.db'))})
    async def exercise():
        async with app.router.lifespan_context(app):
            for _ in range(100):
                if calls:break
                await asyncio.sleep(.01)
            assert calls==['alerts','reports']
            assert not app.state.reel_monitor.done()
        assert app.state.reel_monitor.done()
    asyncio.run(exercise())
