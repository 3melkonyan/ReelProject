"""Language selection over the real HTTP boundary with temporary configuration only."""
import json
from urllib.request import Request

import yaml

from reel.i18n import catalog
from test_web_upload import web


def configure(web, language):
    path = web.root / "config.yaml"
    config = yaml.safe_load(path.read_text())
    config.pop("ui_language", None)
    if language is not None:
        config["ui_language"] = language
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def messages(client, cookie=None):
    headers = {}
    if cookie:
        from urllib.request import HTTPCookieProcessor
        stored = [item.name + '=' + item.value for handler in client.opener.handlers
                  if isinstance(handler, HTTPCookieProcessor) for item in handler.cookiejar if item.name != 'reel-language']
        headers['Cookie'] = '; '.join(stored + ['reel-language=' + cookie])
    with client.opener.open(Request(client.base + "/i18n-data.js", headers=headers), timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"
        body = response.read().decode()
    assert body.startswith("window.REEL_I18N = ") and body.endswith(";\n")
    return json.loads(body[len("window.REEL_I18N = "):-2])


def test_armenian_default_english_config_and_browser_override(web):
    client = web.client()
    configure(web, None)
    data = messages(client)
    assert data["language"] == "hy"
    assert data["messages"] == dict(catalog("hy"))
    assert client.call("GET", "/api/privacy/policy")[1]["notice"] == catalog("hy")["privacy.lessons_are_recorded_and_faces_are_blurred"]
    configure(web, "en")
    data = messages(client)
    assert data["language"] == "en" and data["messages"] == dict(catalog("en"))
    assert messages(client, "hy")["language"] == "hy"
    assert messages(client, "../en")["language"] == "en"
    assert messages(client, "unsupported")["language"] == "en"
    assert messages(client)["language"] == "en"


def test_language_preference_follows_account_and_overrides_browser(web):
    from contextlib import closing
    from reel.db import DB
    from reel.web.accounts import create_user
    password = 'language-fixture-password-123'
    with closing(DB(web.root / 'reel.db').conn) as conn:
        from types import SimpleNamespace
        first = create_user(SimpleNamespace(conn=conn), 'language-one', password,
                            dict(name='First', email='first@example.test', role='student', groups=['Group-A']))
        create_user(SimpleNamespace(conn=conn), 'language-two', password,
                    dict(name='Second', email='second@example.test', role='student', groups=['Group-A']))
    client = web.client()
    assert client.call('POST', '/api/login', body={'login':'language-one','password':password})[0] == 200
    assert client.call('PUT', '/api/accounts/language', body={'language':'en'}, csrf=False)[0] == 400
    assert client.call('PUT', '/api/accounts/language', body={'language':'../en'})[0] == 400
    assert client.call('PUT', '/api/accounts/language', body={'language':'en'}) == (200, {'language':'en'})
    assert messages(client, 'hy')['language'] == 'en'
    second_device = web.client()
    assert second_device.call('POST', '/api/login', body={'login':'language-one','password':password})[0] == 200
    assert messages(second_device)['language'] == 'en'
    other = web.client()
    assert other.call('POST', '/api/login', body={'login':'language-two','password':password})[0] == 200
    assert messages(other)['language'] == 'hy'
    from reel.language import recipient_language
    assert recipient_language(web.root, 'first@example.test') == 'en'
    assert recipient_language(web.root, 'second@example.test') == 'hy'
    assert client.call('POST', '/api/logout')[0] == 200
    assert client.call('PUT', '/api/accounts/language', body={'language':'hy'})[0] == 401


def test_reset_email_uses_recipient_choice_not_sender_language(web, monkeypatch):
    from contextlib import closing
    from types import SimpleNamespace
    from reel.db import DB
    from reel.web import account_mail, accounts
    from reel.i18n import language_context
    with closing(DB(web.root / 'reel.db').conn) as conn:
        uid = accounts.create_user(SimpleNamespace(conn=conn), 'mail-language', 'email-language-password-123',
            dict(name='Recipient', email='recipient@example.test', role='student', groups=['Group-A']))
        conn.execute('INSERT INTO user_languages VALUES(?,?)', (uid, 'en'))
    monkeypatch.setattr(accounts, 'public_base', lambda root: 'https://example.test')
    monkeypatch.setattr(accounts, 'settings', lambda root: {
        'SMTP_HOST':'smtp.example.test', 'SMTP_USER':'fixture-user', 'SMTP_PASSWORD':'fixture-password',
        'SMTP_FROM':'Reel <sender@example.test>'})
    delivered = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): pass
        def starttls(self, **kwargs): pass
        def login(self, *args): pass
        def send_message(self, message): delivered.append(message)
    monkeypatch.setattr(account_mail.smtplib, 'SMTP', SMTP)
    with language_context('hy'):
        assert account_mail.send_reset(web.root, 'recipient@example.test', 'https://example.test/#/reset/token')
    assert 'Password reset' in delivered[0]['Subject']
    assert 'lang="en"' in delivered[0].get_body(preferencelist=('html',)).get_content()
