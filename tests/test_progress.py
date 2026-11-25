"""Preserved item-4 backend: exact watch unions, private resume and fresh authorization."""
from contextlib import closing

import pytest
from reel import learning
from reel.db import DB
from reel.web import accounts


PASSWORD='only-a-test-password'


def seed(db,group='Group-A',key='lesson'):
    job=db.create_job('fixture.mp4',key,group)
    for stage,out in [('privacy',{}),('metadata',{'title':'Դաս','chapters':[]}),('segments',{'final_sec':100})]:
        db.stage_start(job,stage);db.stage_done(job,stage,out)
    db.add_publication(job,'youtube','abcdefghijk','https://youtu.be/abcdefghijk')
    return job


def member(web,login='progress-user',group='Group-A'):
    db=DB(web.root/'reel.db')
    with closing(db.conn):
        uid=accounts.create_user(db,login,PASSWORD,{'name':'Անի Փորձնական','email':login+'@example.test','role':'student','groups':[group]})
    client=web.client();assert client.call('POST','/api/login',body={'login':login,'password':PASSWORD})[0]==200
    return client,uid


def test_union_never_counts_seek_gaps_or_duplicate_seconds(tmp_path):
    db=DB(tmp_path/'test.db')
    with closing(db.conn):
        uid=accounts.create_user(db,'union-user',PASSWORD,{'name':'Թեստ','email':'union@example.test','role':'student','groups':['Group-A']})
        job=seed(db)
        first=learning.record_progress(db,uid,job,60,100,[[0,10],[11,20]],known_duration=100)
        assert first['watched_sec']==19 and first['position']==60 and first['percent']==19
        again=learning.record_progress(db,uid,job,99,100,[[0,10],[11,20]],known_duration=100)
        assert again['watched_sec']==19 and not again['completed']
        edge=learning.record_progress(db,uid,job,104,10000,[[102,104]],known_duration=100)
        assert edge['watched_sec']==19 and edge['position']==100


def test_many_disjoint_intervals_do_not_recurse_or_fill_gaps(tmp_path):
    db=DB(tmp_path/'test.db')
    with closing(db.conn):
        uid=accounts.create_user(db,'many-user',PASSWORD,{'name':'Թեստ','email':'many@example.test','role':'student','groups':['Group-A']})
        job=seed(db)
        out=learning.record_progress(db,uid,job,1900,2000,[[i*4,i*4+1]for i in range(450)],known_duration=2000)
        assert out['watched_sec']==450 and out['percent']==22.5


@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),-1,106,{},[]])
def test_invalid_positions_leave_progress_unchanged(tmp_path,bad):
    db=DB(tmp_path/'test.db')
    with closing(db.conn):
        uid=accounts.create_user(db,'bad-user',PASSWORD,{'name':'Թեստ','email':'bad@example.test','role':'student','groups':['Group-A']})
        job=seed(db)
        with pytest.raises(ValueError):learning.record_progress(db,uid,job,bad,100,[],known_duration=100)
        assert learning.progress_for(db,uid)=={}


def test_progress_respects_callers_rollback(tmp_path):
    db=DB(tmp_path/'test.db')
    with closing(db.conn):
        uid=accounts.create_user(db,'atomic-user',PASSWORD,{'name':'Թեստ','email':'atomic@example.test','role':'student','groups':['Group-A']})
        job=seed(db);learning.ensure_schema(db.conn);db.conn.execute('BEGIN IMMEDIATE')
        learning.record_progress(db,uid,job,20,100,[[0,20]])
        db.conn.execute('ROLLBACK');assert learning.progress_for(db,uid)=={}


def test_progress_is_per_account_and_only_visible_lessons(web):
    one,uid=member(web);two,other=member(web,'other-user')
    db=DB(web.root/'reel.db')
    with closing(db.conn):
        job=seed(db);foreign=seed(db,'Group-B','foreign')
    payload={'position':50,'duration':100,'intervals':[[0,20]],'user_id':other}
    assert one.call('POST',f'/api/library/{job}/progress',body=payload)[1]['saved']
    assert one.call('GET',f'/api/library/{job}')[1]['progress']['watched_sec']==20
    assert two.call('GET','/api/library/progress')[1]['items']==[]
    assert one.call('GET',f'/api/library/{foreign}')[0]==404
    assert one.call('POST',f'/api/library/{foreign}/progress',body=payload)[0]==404
    assert one.call('POST',f'/api/library/{job}/progress',body=payload,csrf=False)[0]==400
    bad=one.call('POST',f'/api/library/{job}/progress',body={**payload,'position':False})
    assert bad[0]==400 and 'outside' not in str(bad[1])
    legacy=web.client('viewer');assert legacy.call('POST',f'/api/library/{job}/progress',body=payload)==(200,{'saved':False})


@pytest.mark.parametrize('change',["active=0","session_version=session_version+1","groups_json='[\"Group-B\"]'"])
def test_progress_rechecks_account_inside_write_transaction(web,monkeypatch,change):
    from reel.web import learning_api
    client,uid=member(web)
    db=DB(web.root/'reel.db')
    with closing(db.conn):job=seed(db)
    original=learning_api.library_records
    def changed(database,*args,**kwargs):
        records=original(database,*args,**kwargs)
        database.conn.execute('UPDATE users SET '+change+' WHERE id=?',(uid,))
        return records
    monkeypatch.setattr(learning_api,'library_records',changed)
    code,_=client.call('POST',f'/api/library/{job}/progress',body={'position':10,'duration':100,'intervals':[[0,10]]})
    assert code==(404 if change.startswith('groups_json') else 401)
    db=DB(web.root/'reel.db')
    with closing(db.conn):assert learning.progress_for(db,uid)=={}


def test_stale_tab_cannot_credit_the_account_from_another_tab(web):
    old,old_id=member(web,'old-user');current,current_id=member(web,'current-user')
    db=DB(web.root/'reel.db')
    with closing(db.conn):job=seed(db)
    status,_=current.call('POST',f'/api/library/{job}/progress',body={
        'position':60,'duration':100,'intervals':[[0,60]],'expected_user_id':old_id})
    assert status==401
    db=DB(web.root/'reel.db')
    with closing(db.conn):assert learning.progress_for(db,current_id)=={}
    assert old.call('POST',f'/api/library/{job}/progress',body={
        'position':10,'duration':100,'intervals':[[0,10]],'expected_user_id':old_id})[1]['saved']
