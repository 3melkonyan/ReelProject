"""Resumable retirement and cancellation with fake services and temporary job data."""
from contextlib import closing
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from reel.db import DB
from reel import lesson_deletion as deletion, pipeline
from reel.job_lifecycle import job_lock
from test_web_upload import web


def prepared(root):
    db=DB(root/'reel.db');source=root/'recording.mp4';source.write_bytes(b'synthetic recording')
    job=db.create_job(str(source),'recording-hash','Group-A','Python')
    db.set_job(job,status='done')
    for target,external in [('youtube','video-fixture'),('slack','123.456'),('library',None)]:
        db.add_publication(job,target,external,None)
    db.conn.execute("INSERT INTO publication_channels VALUES(?,'slack','C_FIXTURE')",(job,))
    db.conn.execute("INSERT INTO service_messages VALUES(?,'C_SERVICE','789.123',0)",(job,))
    (root/'library.jsonl').write_text(json.dumps({'job_id':job})+'\n'+json.dumps({'job_id':999})+'\n')
    return db,job


def adapters(monkeypatch,fail=None):
    calls=[]
    def run(step,*values):
        calls.append((step,*values))
        if step==fail:raise RuntimeError('synthetic failure')
    youtube=SimpleNamespace(delete_video=lambda value:run('youtube',value))
    slack=SimpleNamespace(delete_message=lambda channel,ts:run('slack',channel,ts))
    monkeypatch.setattr(deletion,'_publishers',lambda:(youtube,slack))
    return calls


def test_all_cleanup_steps_backup_history_and_reupload(tmp_path,monkeypatch):
    db,job=prepared(tmp_path);calls=adapters(monkeypatch)
    try:
        result=deletion.request(db,tmp_path,job,7)
        assert result['state']=='done' and all(v=='done' for v in result['steps'].values())
        backup=json.loads((tmp_path/'archive'/'deleted'/str(job)/'database.json').read_text())
        assert backup['job']['sha256']=='recording-hash' and backup['job']['status']=='done'
        assert len(backup['publications'])==3
        assert ('youtube','video-fixture') in calls
        assert ('slack','C_FIXTURE','123.456') in calls and ('slack','C_SERVICE','789.123') in calls
        assert db.job(job)['sha256']==f'superseded-{job}-recording-hash'
        assert db.publication(job,'old-youtube')['external_id']=='video-fixture'
        assert db.publication(job,'old-slack') and db.publication(job,'old-library')
        assert db.publication(job,'youtube') is None
        assert db.create_job(str(tmp_path/'recording.mp4'),'recording-hash','Group-A')!=job
        assert json.loads((tmp_path/'library.jsonl').read_text())=={'job_id':999}
        assert db.conn.execute("SELECT actor FROM audit_events WHERE action='job.deleted'").fetchone()[0]==7
        before=list(calls)
        assert deletion.request(db,tmp_path,job,7)['state']=='done' and calls==before
    finally:db.conn.close()


@pytest.mark.parametrize('failed',['youtube','slack','library','backup'])
def test_partial_failure_is_visible_and_retries_only_remaining_steps(tmp_path,monkeypatch,failed):
    db,job=prepared(tmp_path);calls=adapters(monkeypatch,failed)
    from reel.publish import library
    original_remove=library.remove_job
    original_backup=deletion._backup
    if failed=='library':monkeypatch.setattr(library,'remove_job',lambda *a:(_ for _ in ()).throw(OSError()))
    if failed=='backup':monkeypatch.setattr(deletion,'_backup',lambda *a:(_ for _ in ()).throw(OSError()))
    try:
        first=deletion.request(db,tmp_path,job,1)
        assert first['state']=='partial' and first['steps'][failed]=='failed'
        assert db.publication(job,'youtube') is not None
        assert db.job(job)['sha256']=='recording-hash'
        old_youtube_calls=calls.count(('youtube','video-fixture'))
        monkeypatch.setattr(library,'remove_job',original_remove);monkeypatch.setattr(deletion,'_backup',original_backup)
        # Same fake clients, now successful.
        def publishers():
            return SimpleNamespace(delete_video=lambda value:calls.append(('youtube',value))),SimpleNamespace(delete_message=lambda ch,ts:calls.append(('slack',ch,ts)))
        monkeypatch.setattr(deletion,'_publishers',publishers)
        assert deletion.request(db,tmp_path,job,2)['state']=='done'
        assert calls.count(('youtube','video-fixture'))==old_youtube_calls+(0 if first['steps'].get('youtube')=='done' else 1)
    finally:db.conn.close()


def test_running_worker_is_stopped_before_remote_deletion(tmp_path,monkeypatch):
    db,job=prepared(tmp_path);calls=adapters(monkeypatch)
    try:
        db.set_job(job,status='running')
        with job_lock(tmp_path,job):
            assert deletion.request(db,tmp_path,job,1)['state']=='waiting'
            assert calls==[] and not (tmp_path/'archive'/'deleted'/str(job)/'database.json').exists()
        assert deletion.resume(tmp_path,job)['state']=='done'
    finally:db.conn.close()


def test_worker_cancels_before_next_stage_or_publication(tmp_path,monkeypatch):
    db=DB(tmp_path/'reel.db');source=tmp_path/'source.mp4';source.write_bytes(b'synthetic')
    job=db.create_job(str(source),'worker-fixture','Group-A');calls=adapters(monkeypatch)
    def first(ctx):
        other=DB(tmp_path/'reel.db')
        try:assert deletion.request(other,tmp_path,job,1)['state']=='waiting'
        finally:other.conn.close()
        from reel.progress import report
        report(.5)
        raise AssertionError('Cancellation must interrupt the stage')
    try:
        assert pipeline.run_job(db,job,[('probe',first),('youtube_upload',lambda _:pytest.fail('must not publish'))],{},tmp_path,notify=lambda _:None)=='stopped'
        assert calls==[] and db.stage(job,'probe')['status']=='cancelled'
        assert deletion.resume(tmp_path,job)['state']=='done'
        assert db.next_runnable() is None
    finally:db.conn.close()


def test_missing_channel_and_ambiguous_publish_are_not_silent(tmp_path,monkeypatch):
    db,job=prepared(tmp_path);adapters(monkeypatch)
    try:
        db.conn.execute('DELETE FROM publication_channels')
        result=deletion.request(db,tmp_path,job,1)
        assert result['state']=='partial' and result['error']=='missing_slack_channel'
        assert deletion.request(db,tmp_path,job,1,'C_ORIGINAL')['state']=='done'
    finally:db.conn.close()


@pytest.mark.parametrize('role',['viewer','uploader','student','lecturer'])
def test_deletion_api_rejects_nonadministrators(web,role):
    from reel.web.accounts import create_user
    database=DB(web.root/'reel.db')
    with closing(database.conn):
        job=database.create_job('synthetic.mp4','permissions-'+role,'Group-A')
        if role in ('lecturer','student'):
            create_user(database,'delete-'+role,'delete-test-password',dict(name=role,email=role+'@example.test',role=role,groups=['Group-A']))
    client=web.client(role) if role in ('viewer','uploader') else web.client()
    if role in ('student','lecturer'):assert client.call('POST','/api/login',body={'login':'delete-'+role,'password':'delete-test-password'})[0]==200
    assert client.call('POST',f'/api/jobs/{job}/delete',body={'confirm':True})[0]==403
    assert client.call('GET',f'/api/jobs/{job}/deletion')[0]==403


def test_admin_api_requires_confirmation_and_returns_retryable_state(web,monkeypatch):
    db,job=prepared(web.root);db.conn.close()
    calls=adapters(monkeypatch,'youtube');admin=web.client('admin')
    endpoint=f'/api/jobs/{job}/delete'
    assert admin.call('POST',endpoint,body={})[0]==400
    status,first=admin.call('POST',endpoint,body={'confirm':True})
    assert status==200 and first['state']=='partial'
    assert admin.call('GET',f'/api/jobs/{job}/deletion')[1]['steps']['youtube']=='failed'
    adapters(monkeypatch)
    assert admin.call('POST',endpoint,body={'confirm':True})[1]['state']=='done'


def test_missing_publication_receipt_blocks_cleanup(tmp_path,monkeypatch):
    db,job=prepared(tmp_path);calls=adapters(monkeypatch)
    try:
        db.conn.execute("DELETE FROM publications WHERE job_id=? AND target='youtube'",(job,))
        folder=tmp_path/'work'/str(job);folder.mkdir(parents=True)
        (folder/'pub_youtube.pending').write_text('{}')
        result=deletion.request(db,tmp_path,job,1)
        assert result['state']=='partial' and result['error']=='unresolved_publication'
        assert calls==[] and db.job(job)['sha256']=='recording-hash'
    finally:db.conn.close()


def test_service_notification_receipt_is_saved(tmp_path,monkeypatch):
    db,job=prepared(tmp_path)
    from reel.stages._dryrun import slack
    monkeypatch.setenv('REEL_DRY_RUN','1')
    monkeypatch.setattr(slack,'notify',lambda text:{'channel':'C_NOTICE','ts':'123.789'})
    try:
        pipeline.notify_for_job(tmp_path,job,'Synthetic notification')
        row=db.conn.execute("SELECT timestamp FROM service_messages WHERE job_id=? AND channel='C_NOTICE'",(job,)).fetchone()
        assert row[0]=='123.789'
    finally:db.conn.close()


def test_remote_delete_adapters_use_stored_identifiers(monkeypatch):
    from reel.publish import youtube,slack
    calls=[]
    execute=SimpleNamespace(execute=lambda **kw:calls.append(('execute',kw)))
    videos=SimpleNamespace(delete=lambda **kw:calls.append(('video',kw)) or execute)
    monkeypatch.setattr(youtube,'_service',lambda:SimpleNamespace(videos=lambda:videos))
    monkeypatch.setattr(slack,'_client',lambda:SimpleNamespace(chat_delete=lambda **kw:calls.append(('slack',kw))))
    youtube.delete_video('synthetic-video')
    slack.delete_message('C_SYNTHETIC','123.987')
    assert calls==[('video',{'id':'synthetic-video'}),('execute',{'num_retries':0}),('slack',{'channel':'C_SYNTHETIC','ts':'123.987'})]


def test_deleted_job_cannot_restart_or_lose_deleted_status(tmp_path,monkeypatch):
    db,job=prepared(tmp_path);adapters(monkeypatch)
    try:
        assert deletion.request(db,tmp_path,job,1)['state']=='done'
        assert pipeline.run_job(db,job,[('probe',lambda _:pytest.fail('must not restart'))],{},tmp_path)=='stopped'
        assert db.job(job)['status']=='deleted'
    finally:db.conn.close()
