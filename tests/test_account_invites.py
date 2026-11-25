"""G-39 account directory/lifecycle on temporary SQLite and the real HTTP boundary."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import time
from urllib.parse import urlencode

from fastapi import HTTPException
import pytest

from reel.db import DB
from reel.web import accounts as a
from test_web_upload import web

PASSWORD = 'account-fixture-password-123'


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    from reel.web import account_mail
    calls = []
    monkeypatch.setenv('REEL_PUBLIC_URL', 'https://academy.example.test')
    monkeypatch.setattr(account_mail, 'validate_email', lambda email: [])
    monkeypatch.setattr(account_mail, 'send_invitation', lambda *args: calls.append(('invite', args)) or True)
    monkeypatch.setattr(account_mail, 'send_reset', lambda *args: calls.append(('reset', args)) or True)
    return calls


def seed(web, login='example-user', *, name='Աննա Հովհաննիսյան', email=None,
         role='student', groups=None, active=True):
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        uid = a.create_user(d, login, PASSWORD, {'name': name, 'email': email or login + '@example.test',
                            'role': role, 'groups': ([] if role == 'admin' else ['Group-A']) if groups is None else groups})
        if not active:d.conn.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
        return uid


def login(web, name):
    client = web.client()
    status, response = client.call('POST', '/api/login', body={'login': name, 'password': PASSWORD})
    assert status == 200, response
    return client


def directory(client, **query):
    status, data = client.call('GET', '/api/accounts/directory?' + urlencode(query))
    assert status == 200, data
    return data


def test_additive_migration_retains_existing_user_and_job(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, groups_json TEXT NOT NULL DEFAULT \'[]\', avatar TEXT, active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login_at TEXT)')
        conn.execute('INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at) VALUES(?,?,?,?,?,?,?)',
                     ('legacy', 'legacy@example.test', 'Գործող անուն', a.DUMMY_HASH, 'lecturer', '["Group-A"]', '2026-01-01'))
    d = DB(path)
    with closing(d.conn):
        row = d.conn.execute('SELECT * FROM users').fetchone()
        assert row['name'] == 'Գործող անուն' and row['password_hash'] == a.DUMMY_HASH
        assert row['groups_json'] == '["Group-A"]' and row['session_version'] == 1
        assert row['removed_at'] is None and row['deleted_at'] is None
        jid = d.create_job('historical.mp4', 'migration-fixture', 'Group-A')
    again = DB(path)
    with closing(again.conn):
        assert again.job(jid)['source_path'] == 'historical.mp4'
        assert again.conn.execute('SELECT count(*) FROM users').fetchone()[0] == 1


def test_directory_unicode_literal_search_filters_pagination_and_global_counts(web):
    seed(web, 'armenian', name='ԱՐՄԵՆ ՄԿՐՏՉՅԱՆ', role='lecturer', groups=['Group-A', 'Group-B'])
    seed(web, 'latin', name='Straße Teacher', email='Latin@Example.test', role='lecturer', groups=['Group-B'])
    seed(web, 'percentage', name='100% անուն', active=False)
    for index in range(5):seed(web, f'student-{index}', name=f'Ուսանող {index}')
    admin = web.client('admin')
    result = directory(admin, tab='users', role='student', page=2, page_size=2)
    assert result['total'] == 6 and len(result['items']) == 2 and result['pages'] == 3
    assert result['page'] == 2 and result['page_size'] == 2
    assert result['counts']['student'] == 6 and result['counts']['lecturer'] == 2
    assert result['counts']['admin'] == 1 and result['counts']['users'] == 9
    assert [item['name'] for item in result['items']] == sorted([item['name'] for item in result['items']],key=lambda name:(name.split()[-1].casefold(),name.casefold()))
    assert directory(admin, q='արմեն')['items'][0]['login'] == 'armenian'
    assert directory(admin, q='STRASSE')['items'][0]['login'] == 'latin'
    assert directory(admin, q='LATIN@EXAMPLE.TEST')['items'][0]['login'] == 'latin'
    assert directory(admin, q='%')['total'] == 1
    assert directory(admin, role='lecturer', group='Group-B')['total'] == 2
    assert directory(admin, role='student', group='Group-B')['total'] == 0
    assert directory(admin, status='inactive')['items'][0]['login'] == 'percentage'
    assert directory(admin, page=99)['items'] == []
    for query in ({'tab':'unknown'}, {'role':'owner'}, {'status':'unknown'}, {'page':0},
                  {'page_size':101}, {'page':'9'*50}, {'q':'a'*255}, {'group_status':'other'}):
        assert admin.call('GET', '/api/accounts/directory?' + urlencode(query))[0] == 400
    encoded = json.dumps(result)
    assert 'password_hash' not in encoded and 'session_version' not in encoded and 'digest' not in encoded


def test_group_choices_completed_membership_and_new_assignment_rules(web):
    from reel.web.group_status import set_completed
    uid = seed(web, 'existing-member', role='lecturer', groups=['Group-A', 'Group-B'])
    d = DB(web.root / 'reel.db')
    with closing(d.conn):set_completed(d, 'Group-B', True)
    admin = web.client('admin')
    assert directory(admin)['groups'] == ['Group-A']
    assert directory(admin, group_status='completed')['groups'] == ['Group-B']
    assert directory(admin, group_status='all')['groups'] == ['Group-A', 'Group-B']
    assert directory(admin, group='Group-B')['total'] == 1
    assert admin.call('POST', f'/api/accounts/users/{uid}', body={'groups':['Group-A', 'Group-B']})[0] == 200
    other = seed(web, 'new-member', role='lecturer')
    assert admin.call('POST', f'/api/accounts/users/{other}', body={'groups':['Group-A', 'Group-B']})[0] == 400
    assert admin.call('POST', '/api/accounts/invitations', body={
        'name':'Նոր անուն', 'email':'new@example.test', 'role':'student', 'groups':['Group-B']})[0] == 400


def test_remove_restore_revokes_all_sessions_and_email_bound_invites(web):
    uid = seed(web)
    first = login(web, 'example-user');second = login(web, 'example-user')
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        original = dict(d.conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
        reset_id, reset = a.issue_token(d, 'reset', user_id=uid)
        invite_id, invitation = a.issue_token(d, 'invite', {'name':original['name'], 'email':original['email'].upper(),
                                          'role':'student', 'groups':['Group-A']})
        untouched_id, _ = a.issue_token(d, 'invite', {'email':'unrelated@example.test'})
    admin = web.client('admin')
    assert admin.call('POST', f'/api/accounts/users/{uid}/remove')[0] == 200
    assert first.call('GET', '/api/me')[0] == second.call('GET', '/api/me')[0] == 401
    assert web.client().call('POST', '/api/login', body={'login':'example-user', 'password':PASSWORD})[0] == 401
    assert directory(admin)['counts']['removed'] == 1
    removed = directory(admin, tab='removed')['items'][0]
    assert removed['removed'] and not removed['active'] and not removed['deleted']
    assert removed['removed_at']
    assert not any(item['id'] == uid for item in directory(admin)['items'])
    assert admin.call('POST', f'/api/accounts/users/{uid}', body={'active':True})[0] == 409
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        row = d.conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        assert row['session_version'] == original['session_version'] + 1
        assert all(d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0]
                   for tid in (reset_id, invite_id))
        assert d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (untouched_id,)).fetchone()[0] is None
    assert admin.call('POST', f'/api/accounts/users/{uid}/restore')[0] == 200
    assert first.call('GET', '/api/me')[0] == 401
    assert login(web, 'example-user').call('GET', '/api/me')[0] == 200
    assert directory(admin, tab='removed')['total'] == 0
    assert web.client().call('POST', '/api/accounts/reset', body={'token':reset, 'password':PASSWORD})[0] == 400
    assert web.client().call('POST', '/api/accounts/invitation', body={'token':invitation})[0] == 400
    assert admin.call('POST', f'/api/accounts/users/{uid}/restore')[0] == 409


def test_delete_is_removed_only_anonymizes_tokens_and_retains_history(web):
    uid = seed(web, name='Հին անձնական անուն', email='private-person@example.test')
    admin = web.client('admin')
    assert admin.call('POST', f'/api/accounts/users/{uid}/delete')[0] == 409
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        jid = d.create_job('historical-upload.mp4', 'anonymization-history', 'Group-A')
        d.stage_start(jid, 'metadata');d.stage_done(jid, 'metadata', {'title':'Պահպանվող դաս'})
        d.add_publication(jid, 'youtube', 'historical-video', 'https://example.test/video')
        token_id, _ = a.issue_token(d, 'invite', {'name':'Հին անձնական անուն', 'email':'PRIVATE-PERSON@EXAMPLE.TEST',
                                               'role':'student', 'groups':['Group-A']}, user_id=uid, actor=1)
        d.conn.execute('UPDATE account_tokens SET used_at=?,registered_user_id=? WHERE id=?', (time.time(), uid, token_id))
        pending_id, _ = a.issue_token(d, 'invite', {'name':'Հին անձնական անուն', 'email':'private-person@example.test',
                                                 'role':'student', 'groups':['Group-A']})
        history = [tuple(row) for row in d.conn.execute('SELECT * FROM jobs')]
    avatars = web.root / 'avatars';avatars.mkdir()
    avatar = avatars / f"{uid}-{'a' * 32}.jpg"
    avatar.write_bytes(b'private-avatar')
    unrelated = avatars / f"999-{'b' * 32}.jpg";unrelated.write_bytes(b'keep-avatar')
    outside = web.root / 'outside.jpg';outside.write_bytes(b'keep-outside')
    (avatars / f"{uid}-{'c' * 32}.jpg").symlink_to(outside)
    assert admin.call('POST', f'/api/accounts/users/{uid}/remove')[0] == 200
    assert admin.call('POST', f'/api/accounts/users/{uid}/delete')[0] == 200
    detail = admin.call('GET', f'/api/accounts/users/{uid}')[1]
    assert detail['name'] == a.DELETED_USER_LABEL and detail['deleted'] and detail['removed']
    assert detail['email'] != 'private-person@example.test' and detail['groups'] == [] and detail['avatar'] is None
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        row = d.conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        assert not row['active'] and not a.verify_password(PASSWORD, row['password_hash'])
        tokens = d.conn.execute('SELECT * FROM account_tokens WHERE id IN (?,?)', (token_id, pending_id)).fetchall()
        serialized = json.dumps([dict(token) for token in tokens], ensure_ascii=False).casefold()
        assert 'private-person@example.test' not in serialized and 'Հին անձնական անուն'.casefold() not in serialized
        assert all(token['revoked_at'] is not None for token in tokens)
        assert tokens[0]['registered_user_id'] == uid
        assert history == [tuple(record) for record in d.conn.execute('SELECT * FROM jobs')]
        assert d.stage_output(jid, 'metadata') == {'title':'Պահպանվող դաս'}
        assert d.publication(jid, 'youtube')['external_id'] == 'historical-video'
    assert directory(admin, tab='removed')['total'] == 0
    assert not avatar.exists() and unrelated.read_bytes() == b'keep-avatar' and outside.read_bytes() == b'keep-outside'
    assert admin.call('POST', f'/api/accounts/users/{uid}/restore')[0] == 404
    assert admin.call('POST', f'/api/accounts/users/{uid}/delete')[0] == 404


def test_self_last_admin_auth_csrf_and_missing_user_protections(web):
    admin = web.client('admin')
    for action in ('remove', 'delete'):
        assert admin.call('POST', f'/api/accounts/users/1/{action}')[0] == 409
    assert admin.call('POST', '/api/accounts/users/1', body={'active':False})[0] == 409
    assert admin.call('POST', '/api/accounts/users/1', body={'role':'student', 'groups':['Group-A']})[0] == 409
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        row = d.conn.execute('SELECT * FROM users WHERE id=1').fetchone()
        with pytest.raises(HTTPException) as caught:a.protect_admin(d, row, 999)
        assert caught.value.status_code == 409
    uid = seed(web);student = login(web, 'example-user')
    for path in ('/api/accounts/directory', f'/api/accounts/users/{uid}'):
        assert web.client().call('GET', path)[0] == 401
        assert student.call('GET', path)[0] == 403
    for suffix in ('remove', 'restore', 'delete', 'reset'):
        path = f'/api/accounts/users/{uid}/{suffix}'
        assert web.client().call('POST', path)[0] == 401
        assert student.call('POST', path)[0] == 403
        assert admin.call('POST', path, csrf=False)[0] == 400
        assert admin.call('POST', f'/api/accounts/users/9999/{suffix}')[0] == 404
    assert student.call('POST', '/api/accounts/invitations/999/resend')[0] == 403
    assert admin.call('POST', '/api/accounts/invitations/999/resend', csrf=False)[0] == 400
    assert admin.call('POST', '/api/accounts/invitations/999/resend')[0] == 404


def test_invite_autosend_resend_rotates_tokens_and_filters_status(web, mail, monkeypatch):
    from reel.web import account_mail
    monkeypatch.setattr(account_mail, 'validate_email', lambda email: ['Փորձնական DNS զգուշացում։'])
    admin = web.client('admin')
    payload = {'name':'Հրավիրված անձ', 'email':'Invited@Example.test', 'role':'student', 'groups':['Group-A']}
    status, first = admin.call('POST', '/api/accounts/invitations', body=payload)
    assert status == 200 and first['mail_sent'] and first['public_available']
    assert first['warnings'] == ['Փորձնական DNS զգուշացում։']
    assert first['link'].startswith('https://academy.example.test/#/invite/')
    assert mail[-1][0] == 'invite' and mail[-1][1][1:] == ('invited@example.test', first['link'], payload['name'])
    assert admin.call('POST', '/api/accounts/invitations', body=payload)[0] == 409
    code, second = admin.call('POST', f"/api/accounts/invitations/{first['id']}/resend")
    assert code == 200 and second['mail_sent'] and second['id'] != first['id'] and second['link'] != first['link']
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        rows = d.conn.execute('SELECT * FROM account_tokens ORDER BY id').fetchall()
        assert rows[0]['revoked_at'] and rows[1]['expires_at'] - rows[1]['created_at'] == 72 * 3600
        assert first['link'].rsplit('/', 1)[1] not in str([dict(row) for row in rows])
    assert directory(admin, tab='invitations', status='pending')['total'] == 1
    assert directory(admin, tab='invitations', status='revoked')['total'] == 1
    assert directory(admin, tab='invitations', q='ՀՐԱՎԻՐՎԱԾ')['total'] == 2
    raw_first = first['link'].rsplit('/', 1)[1];raw_second = second['link'].rsplit('/', 1)[1]
    assert web.client().call('POST', '/api/accounts/invitation', body={'token':raw_first})[0] == 400
    code, registered = web.client().call('POST', '/api/accounts/register', body={'recording_consent':True,
        'token':raw_second, 'login':'invited-account', 'password':PASSWORD, 'role':'admin', 'groups':['Group-B']})
    assert code == 200 and registered['user']['role'] == 'student' and registered['user']['groups'] == ['Group-A']
    assert directory(admin, tab='invitations', status='used')['items'][0]['registered_login'] == 'invited-account'
    assert admin.call('POST', f"/api/accounts/invitations/{second['id']}/resend")[0] == 409


@pytest.mark.parametrize('base,available', [('', False), ('http://localhost:8080', False),
                                           ('http://127.0.0.1:8080', False), ('https://public.example.test', True)])
def test_mail_delivery_failure_and_public_link_status(web, mail, monkeypatch, base, available):
    from reel.web import account_mail
    monkeypatch.setenv('REEL_PUBLIC_URL', base)
    monkeypatch.setattr(account_mail, 'send_invitation', lambda *args: False)
    admin = web.client('admin')
    status, result = admin.call('POST', '/api/accounts/invitations', body={
        'name':'Փորձնական անձ', 'email':'delivery@example.test', 'role':'student', 'groups':['Group-A']})
    assert status == 200 and result['mail_sent'] is False
    assert result['public_available'] is available and result['public_access'] is available
    assert '/#/invite/' in result['link']
    uid = seed(web)
    reset_code, reset = admin.call('POST', f'/api/accounts/users/{uid}/reset')
    assert reset_code == 200 and reset['public_available'] is available
    assert reset['mail_sent'] is available


def test_email_validation_failure_and_resend_snapshot_change_issue_no_new_tokens(web, monkeypatch, mail):
    from reel.web import account_mail
    admin = web.client('admin')
    payload = {'name':'Հրավիրված անձ', 'email':'blocked@example.test', 'role':'student', 'groups':['Group-A']}

    def invalid(email):
        raise HTTPException(400, 'Տիրույթը փոստ չի ընդունում։')

    monkeypatch.setattr(account_mail, 'validate_email', invalid)
    assert admin.call('POST', '/api/accounts/invitations', body=payload)[0] == 400
    d = DB(web.root / 'reel.db')
    with closing(d.conn):assert d.conn.execute('SELECT count(*) FROM account_tokens').fetchone()[0] == 0
    assert mail == []
    monkeypatch.setattr(account_mail, 'validate_email', lambda email: [])
    code, invitation = admin.call('POST', '/api/accounts/invitations', body=payload)
    assert code == 200

    def anonymized_during_dns(email):
        d = DB(web.root / 'reel.db')
        with closing(d.conn):
            d.conn.execute('UPDATE account_tokens SET payload=?,revoked_at=? WHERE id=?',
                           (json.dumps({'name':a.DELETED_USER_LABEL, 'email':'', 'role':'student', 'groups':[]}),
                            time.time(), invitation['id']))
        return []

    monkeypatch.setattr(account_mail, 'validate_email', anonymized_during_dns)
    assert admin.call('POST', f"/api/accounts/invitations/{invitation['id']}/resend")[0] == 409
    d = DB(web.root / 'reel.db')
    with closing(d.conn):assert d.conn.execute('SELECT count(*) FROM account_tokens').fetchone()[0] == 1
    assert len(mail) == 1


def test_unchanged_edit_keeps_session_and_tokens_but_real_access_change_revokes(web):
    uid = seed(web, role='lecturer', groups=['Group-A', 'Group-B'])
    client = login(web, 'example-user')
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        version = d.conn.execute('SELECT session_version FROM users WHERE id=?', (uid,)).fetchone()[0]
        tid, _ = a.issue_token(d, 'reset', user_id=uid)
    admin = web.client('admin')
    assert admin.call('POST', f'/api/accounts/users/{uid}', body={
        'role':'lecturer', 'groups':['Group-B', 'Group-A'], 'active':True})[0] == 200
    assert client.call('GET', '/api/me')[0] == 200
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert d.conn.execute('SELECT session_version FROM users WHERE id=?', (uid,)).fetchone()[0] == version
        assert d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0] is None
    assert admin.call('POST', f'/api/accounts/users/{uid}', body={'groups':['Group-B']})[0] == 200
    assert client.call('GET', '/api/me')[0] == 401
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert d.conn.execute('SELECT session_version FROM users WHERE id=?', (uid,)).fetchone()[0] == version + 1
        assert d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0] is not None


def test_completed_group_invitation_cannot_redeem_or_consume_token_until_reopened(web):
    from reel.web.group_status import set_completed
    admin = web.client('admin')
    invitations = []
    for index, group in enumerate(('Group-A', 'Group-B')):
        code, result = admin.call('POST', '/api/accounts/invitations', body={
            'name':'Հրավիրված անձ', 'email':f'completion-{index}@example.test',
            'role':'student', 'groups':[group]})
        assert code == 200
        invitations.append(result)
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        set_completed(d, 'Group-A', True)
        before = [tuple(row) for row in d.conn.execute('SELECT * FROM account_tokens ORDER BY id')]
        count = d.conn.execute('SELECT count(*) FROM users').fetchone()[0]
    registration = {'recording_consent':True,'token':invitations[0]['link'].rsplit('/', 1)[1],
                    'login':'completed-invite', 'password':PASSWORD}
    client = web.client()
    code, result = client.call('POST', '/api/accounts/register', body=registration)
    assert code == 400 and 'ավարտված' in result['detail']
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert [tuple(row) for row in d.conn.execute('SELECT * FROM account_tokens ORDER BY id')] == before
        assert d.conn.execute('SELECT count(*) FROM users').fetchone()[0] == count
        set_completed(d, 'Group-A', False)
    assert client.call('POST', '/api/accounts/register', body=registration)[0] == 200


def test_idna_email_uniqueness_matches_legacy_unicode_without_rewriting_rows(web):
    from reel.db import email_identity
    unicode_email = 'identity@օրինակ.հայ'
    ascii_email = 'identity@' + 'օրինակ.հայ'.encode('idna').decode('ascii')
    assert email_identity(unicode_email) == ascii_email
    uid = seed(web, 'legacy-idna', email=unicode_email)
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        # Emulate pre-G39 storage; migration and queries must not rewrite it.
        d.conn.execute('UPDATE users SET email=? WHERE id=?', (unicode_email, uid))
        before = tuple(d.conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
        tid, raw = a.issue_token(d, 'invite', {'name':'Նույն անձ', 'email':ascii_email,
                                             'role':'student', 'groups':['Group-A']})
        assert a.find_user(d, ascii_email)['id'] == a.find_user(d, unicode_email)['id'] == uid
        with pytest.raises(sqlite3.IntegrityError):
            a.create_user(d, 'duplicate-idna', PASSWORD, {'name':'Նույն անձ', 'email':ascii_email,
                                                        'role':'student', 'groups':['Group-A']})
        assert not d.conn.in_transaction
    admin = web.client('admin')
    assert admin.call('POST', '/api/accounts/invitations', body={
        'name':'Նույն անձ', 'email':ascii_email, 'role':'student', 'groups':['Group-A']})[0] == 409
    assert web.client().call('POST', '/api/accounts/register', body={'recording_consent':True,
        'token':raw, 'login':'duplicate-register', 'password':PASSWORD})[0] == 409
    assert directory(admin, q=unicode_email)['items'][0]['id'] == uid
    assert directory(admin, q=ascii_email)['items'][0]['id'] == uid
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert tuple(d.conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()) == before
        assert d.conn.execute('SELECT used_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0] is None
    # Even an already-duplicated legacy database must remain openable and unchanged.
    other = seed(web, 'legacy-alias', email='unrelated-idna@example.test')
    d = DB(web.root / 'reel.db')
    with closing(d.conn):d.conn.execute('UPDATE users SET email=? WHERE id=?', (ascii_email, other))
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert [row[0] for row in d.conn.execute('SELECT email FROM users WHERE id IN (?,?) ORDER BY id', (uid, other))] == [unicode_email, ascii_email]


@pytest.mark.parametrize('legacy_user', [True, False])
def test_idna_removal_and_anonymization_revoke_unicode_and_ascii_email_only_tokens(web, legacy_user):
    unicode_email = 'revocation@օրինակ.հայ'
    ascii_email = 'revocation@' + 'օրինակ.հայ'.encode('idna').decode('ascii')
    uid = seed(web, 'idna-revocation', email=unicode_email)
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        if legacy_user:d.conn.execute('UPDATE users SET email=? WHERE id=?', (unicode_email, uid))
        ids = []
        for email in (unicode_email, ascii_email):
            payload = {'name':'Հին անուն', 'email':email, 'role':'student', 'groups':['Group-A']}
            tid, _ = a.issue_token(d, 'invite', payload)
            # Preserve raw legacy Unicode payload, independent of new-token normalization.
            d.conn.execute('UPDATE account_tokens SET payload=? WHERE id=?', (json.dumps(payload), tid))
            ids.append(tid)
        untouched, _ = a.issue_token(d, 'invite', {'email':'other@example.test'})
    admin = web.client('admin')
    assert admin.call('POST', f'/api/accounts/users/{uid}/remove')[0] == 200
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        for tid in ids:
            assert d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0] is not None
        assert d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (untouched,)).fetchone()[0] is None
        assert json.loads(d.conn.execute('SELECT payload FROM account_tokens WHERE id=?', (ids[0],)).fetchone()[0])['email'] == unicode_email
        assert d.conn.execute('SELECT email FROM users WHERE id=?', (uid,)).fetchone()[0] == (unicode_email if legacy_user else ascii_email)
    assert admin.call('POST', f'/api/accounts/users/{uid}/delete')[0] == 200
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        for tid in ids:
            payload = json.loads(d.conn.execute('SELECT payload FROM account_tokens WHERE id=?', (tid,)).fetchone()[0])
            assert payload['email'] == '' and payload['name'] == a.DELETED_USER_LABEL


def test_idna_duplicate_invites_and_resend_match_legacy_payload_aliases(web, mail):
    unicode_email = 'invitation@օրինակ.հայ'
    ascii_email = 'invitation@' + 'օրինակ.հայ'.encode('idna').decode('ascii')
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        ids = []
        for email in (unicode_email, ascii_email):
            payload = {'name':'Հրավիրված անձ', 'email':email, 'role':'student', 'groups':['Group-A']}
            tid, _ = a.issue_token(d, 'invite', payload)
            d.conn.execute('UPDATE account_tokens SET payload=? WHERE id=?', (json.dumps(payload), tid))
            ids.append(tid)
    admin = web.client('admin')
    assert admin.call('POST', '/api/accounts/invitations', body={
        'name':'Հրավիրված անձ', 'email':ascii_email, 'role':'student', 'groups':['Group-A']})[0] == 409
    code, result = admin.call('POST', f'/api/accounts/invitations/{ids[0]}/resend')
    assert code == 200 and result['mail_sent']
    assert mail[-1][1][1] == ascii_email
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        assert all(d.conn.execute('SELECT revoked_at FROM account_tokens WHERE id=?', (tid,)).fetchone()[0] is not None for tid in ids)
        payload = json.loads(d.conn.execute('SELECT payload FROM account_tokens WHERE id=?', (result['id'],)).fetchone()[0])
        assert payload['email'] == ascii_email
        assert a.fields('Անուն', unicode_email, 'student', ['Group-A'], {'Group-A':{}})['email'] == ascii_email


def test_idna_login_rate_limit_uses_the_same_identity_for_both_domain_spellings():
    unicode_email = 'limited@օրինակ.հայ'
    ascii_email = 'limited@' + 'օրինակ.հայ'.encode('idna').decode('ascii')
    d = DB(':memory:')
    with closing(d.conn):
        for _ in range(5):a.login_limit(d, unicode_email, '127.0.0.1', failed=True)
        with pytest.raises(HTTPException) as caught:a.login_limit(d, ascii_email, '127.0.0.1')
        assert caught.value.status_code == 429
