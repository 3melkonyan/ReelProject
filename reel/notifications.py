"""Independent web-side problem monitor. Never starts/stops the processing worker.

One delivery per incident/recipient/channel. Delivery is claimed durably BEFORE the
external call: a timeout/crash is left for operator review rather than sending duplicates.
"""
from __future__ import annotations
from contextlib import suppress

from reel.i18n import t as _t
import asyncio,json,logging,os,shutil,time
from contextlib import closing
from pathlib import Path
from reel import brand_mail

log=logging.getLogger(__name__)
INTERVAL=60
LOW_DISK_BYTES=5*1024**3


def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS monitor_incidents(key TEXT PRIMARY KEY, active INTEGER NOT NULL, generation INTEGER NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS notification_delivery(key TEXT NOT NULL, recipient TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(key,recipient,channel))')


def once(conn,key,recipient,channel,send):
    ensure(conn)
    cur=conn.execute("INSERT OR IGNORE INTO notification_delivery VALUES(?,?,?,'sending',?)",(key,recipient,channel,time.time()))
    if not cur.rowcount:return False
    try:ok=bool(send())
    except Exception:ok=False
    conn.execute('UPDATE notification_delivery SET state=? WHERE key=? AND recipient=? AND channel=?',('sent' if ok else 'failed',key,recipient,channel))
    from reel.audit import audit_connection
    audit_connection(conn,None,'alert.delivery' if key.startswith('alert:') else 'report.delivery','notification',{'status':'sent' if ok else 'failed','target':channel})
    return ok


def slack_admin(email,text):
    """Direct message to the administrator, never to a class/operator group channel."""
    from reel.publish.slack import _client
    client=_client();user=client.users_lookupByEmail(email=email)['user']['id']
    channel=client.conversations_open(users=user)['channel']['id']
    result=client.chat_postMessage(channel=channel,text=text,mrkdwn=False,unfurl_links=False,unfurl_media=False)
    return bool(result.get('ts'))


def worker_alive(root):
    try:
        value=json.loads((Path(root)/'out/worker.json').read_text());pid=value.get('pid')
        if isinstance(pid,bool) or not isinstance(pid,int) or pid<=0:return False
        os.kill(pid,0);return True
    except (OSError,ValueError,TypeError):return False


def conditions(conn,root,*,alive=None,free=None):
    alive=worker_alive(root) if alive is None else alive
    free=shutil.disk_usage(root).free if free is None else free
    found={}
    if free<LOW_DISK_BYTES:found['disk']=_t('text.notifications.less_than_5_gb_of_free_disk')
    # An idle intentionally stopped worker is not an incident.
    if not alive and conn.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
        found['worker']=_t('text.notifications.the_processing_worker_is_not_running_while')
    for row in conn.execute("SELECT job_id,name FROM stages WHERE status='failed' AND (name LIKE 'youtube%' OR name LIKE 'slack%')"):
        found[f'connection:{row[0]}:{row[1]}']=f"{_t('ui.app.lesson')} {row[0]}․ "+('YouTube' if row[1].startswith('youtube') else 'Slack')+f" {_t('text.notifications.connection_or_publication_error_check_the_dashboard')}"
    return found


def poll(conn,root,*,alive=None,free=None,email=None,slack=None):
    ensure(conn);active=conditions(conn,root,alive=alive,free=free)
    # Values sent outside do not include raw errors, tokens, paths or transcript contents.
    for old in conn.execute('SELECT key FROM monitor_incidents WHERE active=1').fetchall():
        if old[0] not in active:conn.execute('UPDATE monitor_incidents SET active=0 WHERE key=?',(old[0],))
    for key,text in active.items():
        conn.execute('INSERT INTO monitor_incidents VALUES(?,1,1) ON CONFLICT(key) DO UPDATE SET generation=generation+CASE WHEN active=0 THEN 1 ELSE 0 END,active=1',(key,))
        gen=conn.execute('SELECT generation FROM monitor_incidents WHERE key=?',(key,)).fetchone()[0]
        for address in brand_mail.admin_emails(conn):
            from reel.language import recipient_language
            from reel.i18n import language_context
            with language_context(recipient_language(root,address)):
                text=conditions(conn,root,alive=alive,free=free).get(key,text)
                event=f'alert:{key}:{gen}'
                once(conn,event,address,'email',lambda address=address, text=text: email(address,text) if email else brand_mail.send(root,address,_t('text.notifications.reel_issue_alert'),eyebrow=_t('text.notifications.administrators_only'),title=_t('text.notifications.attention_required'),paragraphs=[text],note=_t('text.notifications.duplicate_emails_will_not_be_sent_for')))
                once(conn,event,address,'slack',lambda address=address, text=text: (slack or slack_admin)(address,'⚠️ Reel · '+text))
    return len(active)


def install(app,env):
    """Run while the WEB process lives, independently of the worker. No import-time I/O."""
    async def loop():
        while True:
            try:
                def tick():
                    with closing(env['db']().conn) as conn:
                        poll(conn,env['ROOT'])
                        from reel.lesson_report import poll as reports
                        reports(conn,env['ROOT'])
                        tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        if 'lecturer_reviews' in tables:
                            from reel.web.job_features import process_review
                            for row in conn.execute("SELECT job_id FROM lecturer_reviews WHERE decision IS NULL AND (status='pending' OR (status='scanning' AND started_at<?)) LIMIT 1",(time.time()-600,)).fetchall():
                                process_review(env['ROOT'],row[0])
                        if 'lesson_deletions' in tables:
                            from reel.lesson_deletion import resume
                            for row in conn.execute("SELECT job_id FROM lesson_deletions WHERE state IN ('requested','waiting','running') LIMIT 10").fetchall():
                                resume(env['ROOT'],row[0])
                await asyncio.to_thread(tick)
            except asyncio.CancelledError:raise
            except Exception as exc:log.warning('monitor check failed (%s)',type(exc).__name__)
            await asyncio.sleep(INTERVAL)
    async def startup():app.state.reel_monitor=asyncio.create_task(loop())
    async def shutdown():
        task=getattr(app.state,'reel_monitor',None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    from contextlib import asynccontextmanager
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application) as state:
            await startup()
            try:yield state
            finally:await shutdown()
    app.router.lifespan_context=lifespan
