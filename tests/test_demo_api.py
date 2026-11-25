"""Metadata-only demo API obeys the same personal group boundary as the library."""
from contextlib import closing

import pytest

from reel.db import DB
from reel.demo import read_demo_lessons
from reel.web.accounts import create_user



@pytest.mark.parametrize('role', ['student', 'lecturer', 'admin'])
def test_demo_cards_are_scoped_and_never_playable(web, role):
    db = DB(web.root / 'reel.db')
    with closing(db.conn):
        create_user(db, 'demo-api-user', 'only-a-test-password', {
            'name': 'Փորձնական օգտատեր', 'email': 'demo-api@example.test', 'role': role,
            'groups': [] if role == 'admin' else ['Group-A']})
        for group in ('Group-A', 'Group-B'):
            db.conn.execute('INSERT INTO demo_lessons(id,group_name,subject,title,description,lesson_date,thumbnail,demo_tag) VALUES(?,?,?,?,?,?,?,?)',
                (group, group, 'Python', 'Ցուցադրական դաս', 'Միայն նկարագրություն', '2026-11-20', '/brand/reel-email.png', 'g40-demo-v1'))
        assert read_demo_lessons(db, groups=[]) == []
    client = web.client()
    assert client.call('POST', '/api/login', body={'login': 'demo-api-user', 'password': 'only-a-test-password'})[0] == 200
    status, cards = client.call('GET', '/api/demo/lessons')
    assert status == 200 and len(cards) == (2 if role == 'admin' else 1)
    if role != 'admin':
        assert cards[0]['group'] == 'Group-A'
    assert all(card['demo'] and not card['playable'] and card['url'] is None for card in cards)
    assert all('password' not in card and 'email' not in card for card in cards)
    assert 'demo-api@example.test' not in str(cards)
    assert client.call('GET', '/api/group-cards')[0] == (403 if role == 'student' else 200)
    db = DB(web.root / 'reel.db')
    with closing(db.conn):
        assert db.conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.conn.execute('SELECT count(*) FROM publications').fetchone()[0] == 0


@pytest.mark.parametrize('role', [None, 'uploader'])
def test_demo_api_denies_anonymous_and_uploader(web, role):
    assert web.client(role).call('GET', '/api/demo/lessons')[0] == (401 if role is None else 403)
