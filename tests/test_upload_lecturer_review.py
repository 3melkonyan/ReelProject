"""Early scan, first-answer ownership and local reference retention on synthetic identities."""
from contextlib import closing
import json
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from reel.db import DB
from reel import lecturer_review as reviews, lecturer
from reel.pipeline import Ctx
from reel.web.accounts import create_user
from test_web_upload import web


def prepare(root, uploader=None):
    database=DB(root/'reel.db')
    source=root/'synthetic.mp4';source.write_bytes(b'synthetic')
    job=database.create_job(str(source),'synthetic-review-'+str(uploader),'Group-A','Python')
    reviews.register(database,job,uploader)
    folder=reviews.directory(root,job)/'lecturer_candidates';folder.mkdir()
    centres=np.eye(3,128).tolist()
    (folder/'auto.json').write_text(json.dumps({'identities':[{'presence':30-i*5,'share':.7-i*.1} for i in range(3)],'centres':centres}))
    for rank in range(1,4):
        (folder/f'identity_{rank}.jpg').write_bytes(b'synthetic-image')
        refs=folder/str(rank);refs.mkdir()
        (refs/'ref_0.jpg').write_bytes(b'synthetic-image')
        np.savez(refs/'ref_0.npz',feature=np.asarray(centres[rank-1]).reshape(1,128))
    database.conn.execute("UPDATE lecturer_reviews SET status='ready' WHERE job_id=?",(job,))
    return database,job


def test_quick_scan_reuses_identity_ranking_and_limits_sampling(tmp_path,monkeypatch):
    calls=[]
    sample=lecturer._Samples();sample.frames=20;sample.size=(1920,1080)
    for frame in range(20):
        for person in range(3 if frame<8 else 1):
            sample.t.append(frame*10);sample.frame.append(frame);sample.box.append((10,10,80,80));sample.score.append(.9)
            feature=np.eye(3,128,dtype=np.float32)[person];sample.unit.append(feature);sample.raw.append(feature.reshape(1,128));sample.jpg.append(b'image')
    sample.finish()
    monkeypatch.setattr(lecturer,'_sample',lambda video,every,**kw:calls.append((every,kw)) or sample)
    monkeypatch.setattr(lecturer,'_preview',lambda *a:None)
    monkeypatch.setattr('reel.stages.transcribe.media_duration',lambda _:200)
    result=lecturer.quick_candidates(tmp_path/'synthetic.mp4',tmp_path/'candidates')
    assert calls==[(10.0,{'limit':600.0})]
    assert result['width']==1920 and result['seconds']==600
    assert len(result['identities'])==3
    assert result['identities'][0]['presence']>result['identities'][1]['presence']


def test_each_answer_and_first_answer_wins(tmp_path):
    db,job=prepare(tmp_path)
    try:
        reviews.answer(db,tmp_path,job,{'next':True},11,'lecturer')
        assert reviews.row(db,job)['cursor']==2
        result=reviews.answer(db,tmp_path,job,{'identity':2},11,'lecturer')
        assert result['choice']=={'identity':2}
        saved=json.loads((tmp_path/'work'/str(job)/'lecturer_choice.json').read_text())
        assert saved['centre'][1]==1 and saved['by_id']==11
        assert (tmp_path/'work'/str(job)/'confirmed_lecturer'/'identity.json').is_file()
        with pytest.raises(HTTPException) as conflict: reviews.answer(db,tmp_path,job,{'none':True},12)
        assert conflict.value.status_code==409
        event=db.conn.execute("SELECT actor FROM audit_events WHERE action='lecturer.answer'").fetchone()
        assert event[0]==11
    finally:db.conn.close()


def test_blur_everyone_and_no_answer_fallback(tmp_path):
    db,job=prepare(tmp_path)
    try:
        ctx=Ctx(db,job,tmp_path/'synthetic.mp4',tmp_path/'work'/str(job),'Group-A',{})
        value=reviews.resolve_for_blur(ctx)
        assert value['identity']==1 and value['automatic'] is True
        assert reviews.public(db,tmp_path,job)['automatic']
        assert not (ctx.workdir/'confirmed_lecturer').exists()
    finally:db.conn.close()
    other=tmp_path/'other';other.mkdir()
    db,job=prepare(other)
    try:
        reviews.answer(db,other,job,{'none':True},5)
        ctx=Ctx(db,job,other/'synthetic.mp4',other/'work'/str(job),'Group-A',{})
        assert reviews.resolve_for_blur(ctx)['none']
    finally:db.conn.close()


def users(web):
    database=DB(web.root/'reel.db');ids={}
    with closing(database.conn):
        for name,role in [('owner','lecturer'),('other','lecturer'),('student','student')]:
            ids[name]=create_user(database,'review-'+name,'review-password-123',dict(name=name,email=name+'@example.test',role=role,groups=['Group-A']))
    return ids


def login(web,name):
    client=web.client()
    assert client.call('POST','/api/login',body={'login':'review-'+name,'password':'review-password-123'})[0]==200
    return client


def test_review_permissions_images_privacy_inventory_and_deletion(web):
    ids=users(web);db,job=prepare(web.root,ids['owner']);db.conn.close()
    owner=login(web,'owner');other=login(web,'other');student=login(web,'student');admin=web.client('admin')
    endpoint=f'/api/jobs/{job}/lecturer-review'
    assert owner.call('GET',endpoint)[0]==200
    assert other.call('GET',endpoint)[0]==403
    assert other.call('POST',endpoint,body={'identity':1})[0]==403
    assert student.call('POST',endpoint,body={'none':True})[0]==403
    assert student.call('GET',endpoint+'/images/identity_1.jpg')[0]==403
    assert owner.call('GET',endpoint+'/images/identity_1.jpg')[0]==200
    assert owner.call('POST',endpoint,body={'next':True})[0]==200
    assert admin.call('POST',endpoint,body={'identity':2})[0]==200
    assert owner.call('POST',endpoint,body={'none':True})[0]==409
    assert owner.call('GET','/api/privacy/lecturer-references')[0]==403
    code,data=admin.call('GET','/api/privacy/lecturer-references')
    assert code==200 and data['items'][0]['job_id']==job
    assert student.call('DELETE',f'/api/privacy/lecturer-references/{job}')[0]==403
    assert admin.call('DELETE',f'/api/privacy/lecturer-references/{job}')[0]==200
    assert not (web.root/'work'/str(job)/'confirmed_lecturer').exists()
    assert json.loads((web.root/'work'/str(job)/'lecturer_choice.json').read_text())['none']
    assert admin.call('GET','/api/privacy/lecturer-references')[1]['items']==[]


def test_scan_failure_never_waits_for_a_person(tmp_path,monkeypatch):
    db,job=prepare(tmp_path)
    folder=tmp_path/'work'/str(job)/'lecturer_candidates'
    import shutil
    shutil.rmtree(folder)
    db.conn.execute("UPDATE lecturer_reviews SET status='pending' WHERE job_id=?",(job,))
    monkeypatch.setattr(lecturer,'quick_candidates',lambda *a:(_ for _ in ()).throw(RuntimeError('scan unavailable')))
    ctx=Ctx(db,job,tmp_path/'synthetic.mp4',tmp_path/'work'/str(job),'Group-A',{})
    try:assert reviews.resolve_for_blur(ctx)['none']
    finally:db.conn.close()


def test_interrupted_answer_is_not_overwritten(tmp_path):
    db,job=prepare(tmp_path)
    try:
        reviews.write_choice(tmp_path/'work'/str(job),{'identity':2,'centre':[0,1], 'by':'lecturer','by_id':9,'at':reviews.stamp()})
        with pytest.raises(HTTPException) as conflict:
            reviews.answer(db,tmp_path,job,{'none':True},10)
        assert conflict.value.status_code==409
        assert json.loads(reviews.row(db,job)['decision'])=={'identity':2}
        assert reviews.row(db,job)['answered_by']==9
    finally:db.conn.close()


def test_ready_review_notifies_uploader_and_admin_once(tmp_path,monkeypatch):
    from reel.web import job_features
    db,job=prepare(tmp_path)
    with closing(db.conn):
        owner=create_user(db,'mail-owner','synthetic-password',dict(name='Owner',email='owner@example.test',role='lecturer',groups=['Group-A']))
        create_user(db,'mail-admin','synthetic-password',dict(name='Admin',email='admin@example.test',role='admin',groups=[]))
        db.conn.execute('UPDATE lecturer_reviews SET uploader_id=?,status=? WHERE job_id=?',(owner,'pending',job))
    monkeypatch.setattr(lecturer,'quick_candidates',lambda *a:{})
    calls=[]
    monkeypatch.setattr(job_features,'send_review_mail',lambda root,address,jid:calls.append((address,jid)) or True)
    job_features.process_review(tmp_path,job)
    job_features.process_review(tmp_path,job)
    assert sorted(calls)==[('admin@example.test',job),('owner@example.test',job)]
    with closing(DB(tmp_path/'reel.db').conn) as conn:
        assert conn.execute('SELECT count(*) FROM in_app_notifications WHERE job_id=?',(job,)).fetchone()[0]==2


def test_unanswered_review_passes_top_identity_to_blur(tmp_path,monkeypatch):
    import reel.blur as blur_module
    import reel.stages as stages
    db,job=prepare(tmp_path)
    ctx=Ctx(db,job,tmp_path/'synthetic.mp4',tmp_path/'work'/str(job),'Group-A',{})
    ctx._outputs['render']={'final':str(tmp_path/'processed.mp4')}
    ctx._outputs['segments']={}
    received=[]
    def discover(src,folder,choice=None,**kwargs):
        assert choice['identity']==1 and choice['automatic']
        assert choice['centre'][0]==1
        return {'mode':'chosen','refs_dir':str(folder),'lecturer_rank':1}
    monkeypatch.setattr(lecturer,'discover',discover)
    monkeypatch.setattr(lecturer,'track_refs',lambda *a,**kw:0)
    monkeypatch.setattr(blur_module,'analyze',lambda *a,**kw:[{'lecturer':True}])
    monkeypatch.setattr(blur_module,'blur_faces',lambda src,out,refs,**kw:received.append(refs) or {})
    try:
        result=stages.blur(ctx)
        assert result['lecturer_mode']=='chosen' and received[0]
    finally:db.conn.close()


def test_simultaneous_answers_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    db,job=prepare(tmp_path);db.conn.close();barrier=Barrier(2)
    def decide(actor):
        connection=DB(tmp_path/'reel.db')
        try:
            barrier.wait(timeout=5)
            try:
                reviews.answer(connection,tmp_path,job,{'identity':actor},actor,'admin')
                return 200
            except HTTPException as error:return error.status_code
        finally:connection.conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide,[1,2]))==[200,409]
    with closing(DB(tmp_path/'reel.db').conn) as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='lecturer.answer'").fetchone()[0]==1


@pytest.mark.parametrize('language',['hy','en'])
def test_review_email_uses_recipient_language(tmp_path,monkeypatch,language):
    from reel.web.job_features import send_review_mail
    from reel import brand_mail
    from reel.i18n import language_context,t
    monkeypatch.delenv('REEL_DRY_RUN',raising=False)
    monkeypatch.setattr('reel.language.recipient_language',lambda root,address:language)
    received=[]
    monkeypatch.setattr(brand_mail,'send',lambda *a,**kw:received.append((a,kw)) or True)
    assert send_review_mail(tmp_path,'synthetic@example.test',42)
    with language_context(language):
        assert received[0][0][2]==t('review.mail_subject')
        assert received[0][1]['paragraphs']==[t('review.mail_body',job=42)]
