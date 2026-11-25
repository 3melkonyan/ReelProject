"""Groups: temporary DB/config, exact membership, dates and fake expiry delivery."""
from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import threading
from copy import deepcopy
from datetime import date
import json
import sys

from fastapi import HTTPException
import pytest

from reel.db import DB
from reel.web import accounts, admin, group_management as gm
from reel.web.group_status import completed_groups, set_completed
from test_admin_api import group_url

GROUP = {'subjects':[{'name':'Python','playlist':'PL-private-python'}, {'name':'JavaScript','playlist':'PL-private-js'}],
         'slack_channel':'C-private-channel', 'lecturers':[]}
PASSWORD = 'groups-fixture-password'


@pytest.fixture
def database(tmp_path):
    d = DB(tmp_path / 'fixture.db')
    try:yield d
    finally:d.conn.close()


def person(d, login, role, groups, *, active=True):
    uid = accounts.create_user(d, login, PASSWORD, {'name':login, 'email':login+'@example.test', 'role':role, 'groups':groups})
    if not active:d.conn.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
    return uid


def payload(lecturer=None, tas=(), *, start='2026-01-01', end='2026-12-31'):
    return {'course_start':start, 'course_end':end, 'subjects':[
        {'name':'Python','lecturer_id':lecturer,'start_date':start,'end_date':end},
        {'name':'JavaScript','lecturer_id':None,'start_date':None,'end_date':None}], 'ta_ids':list(tas)}


def app_module(admin_web):
    return next(module for module in list(sys.modules.values())
                if getattr(module,'__name__','').startswith('_reel_admin_web_') and getattr(module,'ROOT',None)==admin_web.root)


@pytest.fixture
def http(admin_web, monkeypatch):
    notes = []
    monkeypatch.setattr(app_module(admin_web), 'notify_group_expiry', lambda channel,text:notes.append((channel,text)), raising=False)
    from reel.publish import slack
    monkeypatch.setattr(slack, '_send', lambda *args,**kwargs:pytest.fail('group tests must not contact live Slack'))
    return admin_web, notes


def test_team_assignments_are_exact_members_and_display_only(database):
    lecturer = person(database,'teacher','lecturer',['Group-A'])
    other = person(database,'other-teacher','lecturer',['Group-B'])
    ta = person(database,'assistant','student',['Group-A'])
    administrator = person(database,'all-groups-admin','admin',[])
    before = [tuple(row) for row in database.conn.execute('SELECT * FROM users')]
    result = gm.save_details(database,'Group-A',GROUP,payload(lecturer,[ta]),today=date(2026,11,20))
    assert result['subjects'][0]['lecturer_id'] == lecturer
    assert result['team']['lecturers'][0]['id'] == lecturer
    assert result['team']['tas'][0]['id'] == ta
    assert result['counts']['students'] == 1
    assert {row['id'] for row in gm.eligible_people(database,'Group-A','lecturer')} == {lecturer}
    assert other not in {row['id'] for row in gm.eligible_people(database,'Group-A','lecturer')}
    assert administrator not in {row['id'] for row in gm.eligible_people(database,'Group-A','lecturer')}
    assert before == [tuple(row) for row in database.conn.execute('SELECT * FROM users')]
    encoded = json.dumps(result)
    assert 'password_hash' not in encoded and '@example.test' not in encoded and 'session_version' not in encoded
    database.conn.execute('UPDATE users SET active=0 WHERE id=?',(lecturer,))
    database.conn.execute("UPDATE users SET role='lecturer' WHERE id=?",(ta,))
    fresh = gm.group_snapshot(database,'Group-A',GROUP)
    assert fresh['team'] == {'lecturers':[],'tas':[]} and fresh['subjects'][0]['lecturer_id'] is None


@pytest.mark.parametrize('role,groups,active,slot', [
    ('admin',[],True,'lecturer'), ('student',['Group-A'],True,'lecturer'),
    ('lecturer',['Group-B'],True,'lecturer'), ('lecturer',['Group-A'],False,'lecturer'),
    ('lecturer',['Group-A'],True,'ta'), ('student',['Group-B'],True,'ta'), ('student',['Group-A'],False,'ta')])
def test_wrong_role_inactive_or_other_group_assignment_rolls_back(database,role,groups,active,slot):
    uid = person(database,'ineligible-person',role,groups,active=active)
    data = payload(uid if slot=='lecturer' else None,[uid] if slot=='ta' else [])
    with pytest.raises(HTTPException) as caught:gm.save_details(database,'Group-A',GROUP,data)
    assert caught.value.status_code == 400
    assert database.conn.execute('SELECT count(*) FROM group_metadata').fetchone()[0] == 0
    assert database.conn.execute('SELECT count(*) FROM audit_events').fetchone()[0] == 0


@pytest.mark.parametrize('change', [
    {'course_start':'2026-02-30'}, {'course_start':'20260101'}, {'course_end':'2025-12-31'},
    {'course_start':None}, {'course_start':True},
    {'subjects':[{'name':'Python','lecturer_id':None,'start_date':'2025-12-31','end_date':'2026-02-01'},
                 {'name':'JavaScript','lecturer_id':None,'start_date':None,'end_date':None}]},
    {'subjects':[{'name':'Python','lecturer_id':None,'start_date':'2026-01-01','end_date':'2027-01-01'},
                 {'name':'JavaScript','lecturer_id':None,'start_date':None,'end_date':None}]},
    {'ta_ids':[True]}, {'subjects':[]},
])
def test_invalid_course_subject_dates_and_ids_preserve_previous_metadata(database,change):
    gm.save_details(database,'Group-A',GROUP,payload())
    before = tuple(database.conn.execute('SELECT * FROM group_metadata').fetchone())
    audit = database.conn.execute('SELECT count(*) FROM audit_events').fetchone()[0]
    invalid = payload();invalid.update(deepcopy(change))
    with pytest.raises(HTTPException) as caught:gm.save_details(database,'Group-A',GROUP,invalid)
    assert caught.value.status_code == 400
    assert tuple(database.conn.execute('SELECT * FROM group_metadata').fetchone()) == before
    assert database.conn.execute('SELECT count(*) FROM audit_events').fetchone()[0] == audit


def test_inclusive_expiry_manual_reactivation_and_extension(database):
    gm.save_details(database,'Group-A',GROUP,payload(end='2026-11-20'),today='2026-11-01')
    assert 'Group-A' not in completed_groups(database,today='2026-11-20')
    assert gm.group_snapshot(database,'Group-A',GROUP,today='2026-11-20')['end_soon']
    assert 'Group-A' in completed_groups(database,today='2026-11-21')
    set_completed(database,'Group-A',False,today='2026-11-21')
    assert 'Group-A' not in completed_groups(database,today='2026-11-22')
    assert gm.group_snapshot(database,'Group-A',GROUP,today='2026-11-22')['manual_active']
    gm.save_details(database,'Group-A',GROUP,payload(end='2027-01-01'),today='2026-11-22')
    assert not gm.group_snapshot(database,'Group-A',GROUP,today='2026-11-22')['manual_active']
    assert 'Group-A' not in completed_groups(database,today='2026-11-22')
    assert 'Group-A' in completed_groups(database,today='2027-01-02')


def test_expiry_notifier_is_injected_deduplicated_and_never_retries_failure(database):
    gm.save_details(database,'Group-A',GROUP,payload(end='2026-01-31'),today='2026-01-01')
    cfg = {'groups':{'Group-A':GROUP}}
    calls = []
    assert gm.notify_expired(database,cfg,None,today='2026-02-01') == []
    assert database.conn.execute('SELECT count(*) FROM group_notifications').fetchone()[0] == 0
    def fail(channel,text):
        calls.append((channel,text));raise RuntimeError('simulated delivery failure')
    from reel.audit import audit_context
    with audit_context(database,actor=41):
        assert gm.notify_expired(database,cfg,fail,today='2026-02-01') == [{'name':'Group-A','sent':False}]
    assert gm.notify_expired(database,cfg,fail,today='2026-02-02') == []
    assert len(calls) == 1 and calls[0][0] == GROUP['slack_channel']
    assert database.conn.execute('SELECT sent_at FROM group_notifications').fetchone()[0] is None
    expiry = database.conn.execute("SELECT actor FROM audit_events WHERE action='group.complete'").fetchone()
    assert expiry['actor'] is None


@pytest.mark.parametrize('marker',['metadata','registry','config'])
def test_demo_expiry_never_calls_or_claims_notifier(database,marker):
    gm.save_details(database,'Group-A',GROUP,payload(end='2026-01-31'),today='2026-01-01',
                    demo_tag='g40-demo-v1' if marker=='metadata' else None)
    group = deepcopy(GROUP)
    if marker=='registry':database.conn.execute("INSERT INTO demo_registry VALUES('group','Group-A','g40-demo-v1','{}')")
    if marker=='config':group['demo']=True
    assert gm.notify_expired(database,{'groups':{'Group-A':group}},lambda *args:pytest.fail('demo expiry notified'),today='2026-02-01') == []
    assert database.conn.execute('SELECT count(*) FROM group_notifications').fetchone()[0] == 0


def test_cards_details_team_scopes_masking_counts_and_readonly_ta(http,monkeypatch):
    admin_web, notes = http
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):
        teacher = person(d,'member-teacher','lecturer',['Group-A'])
        other = person(d,'other-teacher','lecturer',['Group-B'])
        ta = person(d,'member-ta','student',['Group-A'])
        d.create_job('fixture.mp4','groups-count','Group-A')
        d.conn.execute('INSERT INTO demo_lessons(id,group_name,subject,title,description,lesson_date,demo_tag) VALUES(?,?,?,?,?,?,?)',
                       ('fixture-demo','Group-A','Python','Դաս','Նկարագրություն','2026-01-01','g40-demo-v1'))
    admin_client = admin_web.client('admin')
    core = admin_client.call('GET','/api/admin/config')[1]['config']['groups']['Group-A']
    core['subjects'][0]['playlist'] = 'PL-private-python'
    core['slack_channel'] = 'CFIXTURE-private-channel'
    assert admin_client.call('PUT',group_url('Group-A'),core)[0] == 200
    def logged_in(login):
        client = admin_web.client();assert client.call('POST','/api/login',{'login':login,'password':PASSWORD})[0]==200
        return client
    lecturer = logged_in('member-teacher');student = logged_in('member-ta')
    code, details = admin_client.call('GET','/api/admin/group-details/Group-A')
    assert code == 200 and {p['id'] for p in details['options']['lecturers']} == {teacher}
    assert other not in {p['id'] for p in details['options']['lecturers']}
    data = payload(teacher,[ta],end='2020-01-01',start='2019-01-01')
    assert admin_client.call('PUT','/api/admin/group-details/Group-A',data)[0] == 200
    assert student.call('PUT','/api/admin/group-details/Group-A',data)[0] == 403
    assert admin_client.call('PUT','/api/admin/group-details/Group-A',data,csrf=False)[0] == 400
    code, cards = lecturer.call('GET','/api/group-cards?status=completed')
    assert code == 200 and [g['name'] for g in cards['groups']] == ['Group-A']
    card = cards['groups'][0]
    assert card['counts'] == {'students':1,'lessons':2,'classes':2}
    assert card['thumbnail_url'].startswith('/api/group-cards/Group-A/')
    assert card['subjects'][0]['playlist'] == gm._mask('PL-private-python')
    assert card['slack'] == gm._mask('CFIXTURE-private-channel')
    assert 'PL-private-python' not in json.dumps(card)
    # The full IDs are never added by team/calendar endpoints.
    assert 'member-ta@example.test' not in json.dumps(cards)
    assert len(notes) == 1
    assert lecturer.call('GET','/api/group-cards?status=completed')[0] == 200 and len(notes) == 1
    assert student.call('GET','/api/group-cards')[0] == 403
    assert student.call('GET','/api/groups/Group-A/team')[1]['team']['tas'][0]['id'] == ta
    assert student.call('GET','/api/groups/Group-B/team')[0] == 404
    assert lecturer.call('GET','/api/groups/Group-B/team')[0] == 404
    assert admin_web.client().call('GET','/api/groups/Group-A/team')[0] == 401
    from reel.web import thumbnail_styles
    seen = []
    monkeypatch.setattr(thumbnail_styles,'_preview',lambda style:seen.append(style) or b'fixture-png')
    assert lecturer.call('GET',card['thumbnail_url']) == (200,b'fixture-png')
    assert seen and lecturer.call('GET','/api/group-cards/Group-B/thumbnail.png')[0] == 404
    assert admin_client.call('GET','/api/group-cards?status=invalid')[0] == 400
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):assert d.conn.execute('SELECT role FROM users WHERE id=?',(ta,)).fetchone()[0]=='student'


def test_core_rename_delete_membership_guards_and_metadata_save_rollback(http,monkeypatch):
    admin_web, _ = http
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):
        member = person(d,'enrolled-student','student',['Group-A'])
        gm.save_details(d,'Group-A',GROUP,payload(end='2026-11-20'))
        set_completed(d,'Group-A',True)
    client = admin_web.client('admin')
    core = client.call('GET','/api/admin/config')[1]['config']['groups']['Group-A']
    before = (admin_web.root/'config.yaml').read_bytes()
    assert client.call('PUT',group_url('Group-A'),{**core,'rename_to':'Renamed'})[0] == 409
    assert client.call('DELETE',group_url('Group-A'))[0] == 409
    assert (admin_web.root/'config.yaml').read_bytes() == before
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):d.conn.execute("UPDATE users SET groups_json='[]' WHERE id=?",(member,))
    original = admin.save_config
    def fail(*args,**kwargs):raise OSError('fixture disk failure')
    monkeypatch.setattr(admin,'save_config',fail)
    assert client.call('PUT',group_url('Group-A'),{**core,'rename_to':'Renamed'})[0] == 500
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):assert d.conn.execute('SELECT name FROM group_metadata').fetchone()[0]=='Group-A'
    monkeypatch.setattr(admin,'save_config',original)
    assert client.call('PUT',group_url('Group-A'),{**core,'rename_to':'Renamed'})[0] == 200
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):
        assert d.conn.execute('SELECT name FROM group_metadata').fetchone()[0]=='Renamed'
        assert 'Renamed' in completed_groups(d) and 'Group-A' not in completed_groups(d)
    assert client.call('DELETE',group_url('Renamed'))[0] == 200
    d = DB(admin_web.root/'reel.db')
    with closing(d.conn):
        assert d.conn.execute('SELECT count(*) FROM group_metadata').fetchone()[0]==0
        assert 'Renamed' not in completed_groups(d)


def test_expiry_claim_serializes_concurrent_reads_without_holding_db_during_callback(tmp_path):
    path = tmp_path/'notifications.db'
    database = DB(path)
    with closing(database.conn):gm.save_details(database,'Group-A',GROUP,payload(end='2026-01-31'),today='2026-01-01')
    entered, release = threading.Event(), threading.Event()
    calls = []
    def notifier(channel,text):
        calls.append((channel,text));entered.set()
        assert release.wait(3)
    def check():
        d = DB(path)
        with closing(d.conn):return gm.notify_expired(d,{'groups':{'Group-A':GROUP}},notifier,today='2026-02-01')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(check)
        try:
            assert entered.wait(3)
            assert pool.submit(check).result(timeout=3) == []
        finally:release.set()
        assert first.result(timeout=3) == [{'name':'Group-A','sent':True}]
    assert len(calls)==1
    database = DB(path)
    with closing(database.conn):
        assert database.conn.execute('SELECT count(*) FROM group_notifications').fetchone()[0]==1
        assert database.conn.execute("SELECT count(*) FROM audit_events WHERE action='group.complete'").fetchone()[0]==1
