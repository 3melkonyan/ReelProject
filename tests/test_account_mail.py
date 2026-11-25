"""Mail helpers: fake DNS/SMTP only; never read account environment files."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from PIL import Image

from reel.web import account_mail as mail
from reel.web import accounts

BASE = "https://reel.example.test"
CONFIG = {
    "SMTP_HOST": "smtp.example.test",
    "SMTP_PORT": "587",
    "SMTP_USER": "fixture-user",
    "SMTP_PASSWORD": "fixture-secret",
    "SMTP_FROM": "Reel <mail@example.test>",
}


class DNSError(Exception):
    pass


class NoAnswer(DNSError):
    pass


class NXDOMAIN(DNSError):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Real configuration, DNS and SMTP are forbidden")

    monkeypatch.setattr(accounts, "settings", forbidden)
    monkeypatch.setattr(accounts, "public_base", forbidden)
    monkeypatch.setattr(mail, "_dns", forbidden)
    monkeypatch.setattr(mail.smtplib, "SMTP", forbidden)


@pytest.fixture
def dns(monkeypatch):
    resolver = MagicMock()
    resolver.resolve.return_value = [SimpleNamespace(exchange="mx.example.test.")]
    module = SimpleNamespace(
        Resolver=MagicMock(return_value=resolver), NoAnswer=NoAnswer, NXDOMAIN=NXDOMAIN
    )
    monkeypatch.setattr(mail, "_dns", lambda: (module, DNSError))
    return SimpleNamespace(module=module, resolver=resolver)


def link(kind):
    return BASE + "/#/" + kind + "/" + "a" * 43


@pytest.mark.parametrize(
    "domain, suggested",
    [
        ("gamil.com", "gmail.com"),
        ("gmial.com", "gmail.com"),
        ("gmail.con", "gmail.com"),
        ("yahoo.co", "yahoo.com"),
        ("hotmail.co", "hotmail.com"),
        ("outlook.co", "outlook.com"),
    ],
)
def test_common_domain_typos_are_400_with_armenian_suggestion(domain, suggested):
    with pytest.raises(HTTPException) as error:
        mail.validate_email("name@" + domain)
    assert error.value.status_code == 400
    assert "Միգուցե՝ name@" + suggested in error.value.detail


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "not-an-email",
        "a@@example.com",
        ".a@example.com",
        "a..b@example.com",
        "a@example..com",
        "a@-example.com",
        "a@example.com\n",
        "a@example.com\r\nBcc:other@example.com",
        "a@localhost",
        "a@x.123",
        "a" * 65 + "@example.com",
    ],
)
def test_invalid_email_never_reaches_dns(email):
    with pytest.raises(HTTPException) as error:
        mail.validate_email(email)
    assert error.value.status_code == 400


def test_mx_lookup_has_one_three_second_budget_and_no_search_suffix(dns):
    assert mail.validate_email(" User+tag@EXAMPLE.COM ") == []
    dns.resolver.resolve.assert_called_once_with("example.com", "MX", lifetime=3.0, search=False)
    assert dns.resolver.timeout == dns.resolver.lifetime == 3.0


def test_idna_domain_is_resolved_in_ascii(dns):
    assert mail.validate_email("user@օրինակ.հայ") == []
    assert dns.resolver.resolve.call_args.args[0] == "օրինակ.հայ".encode("idna").decode("ascii")


@pytest.mark.parametrize("result", [[], [SimpleNamespace(exchange=".")]])
def test_empty_or_explicit_null_mx_is_rejected(dns, result):
    dns.resolver.resolve.return_value = result
    with pytest.raises(HTTPException) as error:
        mail.validate_email("user@example.com")
    assert error.value.status_code == 400 and "MX" in error.value.detail


@pytest.mark.parametrize("failure", [NoAnswer(), NXDOMAIN()])
def test_no_answer_or_missing_domain_is_rejected(dns, failure):
    dns.resolver.resolve.side_effect = failure
    with pytest.raises(HTTPException) as error:
        mail.validate_email("user@example.com")
    assert error.value.status_code == 400


@pytest.mark.parametrize("failure", [DNSError("timeout"), OSError("offline"), TimeoutError()])
def test_unavailable_dns_returns_warning_instead_of_rejecting(dns, failure):
    dns.resolver.resolve.side_effect = failure
    assert mail.validate_email("user@example.com") == [mail.DNS_WARNING]


def test_unavailable_resolver_configuration_is_fail_open(dns):
    dns.module.Resolver.side_effect = DNSError("no resolver configured")
    assert mail.validate_email("user@example.com") == [mail.DNS_WARNING]


def test_missing_optional_dns_module_returns_warning(monkeypatch):
    def missing():
        raise ImportError("fixture missing dependency")

    monkeypatch.setattr(mail, "_dns", missing)
    assert mail.validate_email("user@example.com") == [mail.DNS_WARNING]


@pytest.mark.parametrize(
    "kind, duration, cta",
    [("invite", "72 ժամ", "Ընդունել հրավերը"), ("reset", "60 րոպե", "Վերականգնել գաղտնաբառը")],
)
@pytest.mark.parametrize("theme", ["auto", "light", "dark"])
def test_multipart_message_has_plain_html_cid_and_bounded_wire_size(kind, duration, cta, theme):
    message = mail.build_message(
        kind, "recipient@example.test", link(kind), "Անի Սարգսյան", theme=theme
    )
    assert message.get_content_type() == "multipart/alternative"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert duration in plain and duration in html and cta in plain and cta in html
    assert link(kind) in plain and link(kind) in html
    assert "Եթե դուք չեք խնդրել, պարզապես անտեսեք այս նամակը" in plain
    assert "Reel" in html and "Reel" in plain
    assert 'role="presentation"' in html and 'style="' in html
    assert 'bgcolor="#000000"' in html and "cid:reel-logo" in html
    assert "<script" not in html.lower() and "<svg" not in html.lower()
    assert len(html.encode("utf-8")) < 100_000 and len(message.as_bytes()) < 100_000
    image = next(part for part in message.walk() if part.get_content_type() == "image/png")
    assert image["Content-ID"] == "<reel-logo>" and image.get_content_disposition() == "inline"
    assert image.get_payload(decode=True) == mail.LOGO.read_bytes()
    reparsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert "Անի Սարգսյան" in reparsed.get_body(preferencelist=("plain",)).get_content()
    assert reparsed["To"] == "recipient@example.test"
    assert ("@media (prefers-color-scheme:dark)" in html) is (theme == "auto")


def test_name_and_url_html_are_escaped_without_config_or_dns_access():
    url = BASE + '/#/invite/"onclick="unsafe'
    html = (
        mail.build_message("invite", "recipient@example.test", url, '<img src=x onerror="unsafe">')
        .get_body(preferencelist=("html",))
        .get_content()
    )
    assert "<img src=x" not in html and "&lt;img src=x" in html
    assert 'href="' + url + '"' not in html
    assert "&quot;onclick=&quot;unsafe" in html


@pytest.mark.parametrize(
    "url",
    [
        "/#/invite/token",
        "javascript:alert(1)",
        "http://attacker.example/#/invite/token",
        "https://user:password@example.test/#/invite/token",
        BASE + "/\nattack",
    ],
)
def test_unsafe_email_links_are_rejected(url):
    with pytest.raises(ValueError):
        mail.build_message("invite", "recipient@example.test", url)


def test_logo_is_small_black_background_white_signal_wordmark():
    assert mail.LOGO.stat().st_size < 30_000
    with Image.open(mail.LOGO) as logo:
        assert logo.format == "PNG" and logo.size == (480, 154)
        assert logo.convert("RGB").getpixel((0, 0)) == (0, 0, 0)
        assert logo.convert("RGB").getextrema() == ((0, 255), (0, 255), (0, 255))


@pytest.fixture
def smtp(monkeypatch):
    events, messages = [], []

    class SMTP:
        def __init__(self, host, port, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append(("close",))

        def ehlo(self):
            events.append(("ehlo",))

        def starttls(self, context):
            events.append(("starttls", context is not None))

        def login(self, user, password):
            events.append(("login", user, password))

        def send_message(self, message):
            events.append(("send",))
            messages.append(message)
            return {}

    monkeypatch.setattr(accounts, "settings", lambda root: dict(CONFIG))
    monkeypatch.setattr(accounts, "public_base", lambda root: BASE)
    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    return SimpleNamespace(events=events, messages=messages, cls=SMTP)


@pytest.mark.parametrize("kind", ["invite", "reset"])
def test_smtp_sends_multipart_over_starttls_after_trusted_base_check(smtp, tmp_path, kind):
    sender = mail.send_invitation if kind == "invite" else mail.send_reset
    assert sender(tmp_path, "recipient@example.test", link(kind), "Անի") is True
    assert smtp.events[0] == ("connect", "smtp.example.test", 587, 10)
    assert [event[0] for event in smtp.events] == [
        "connect",
        "ehlo",
        "starttls",
        "ehlo",
        "login",
        "send",
        "close",
    ]
    assert len(smtp.messages) == 1 and smtp.messages[0].is_multipart()
    assert smtp.messages[0]["From"].addresses[0].addr_spec == "mail@example.test"


@pytest.mark.parametrize(
    "base, url",
    [
        (None, link("invite")),
        ("", link("invite")),
        (BASE, "https://attacker.example/#/invite/token"),
        (BASE, link("reset")),
        (BASE, "/#/invite/token"),
    ],
)
def test_missing_or_inconsistent_public_base_never_contacts_smtp(
    smtp, monkeypatch, tmp_path, base, url
):
    monkeypatch.setattr(accounts, "public_base", lambda root: base)
    assert mail.send_invitation(tmp_path, "recipient@example.test", url) is False
    assert smtp.events == []


@pytest.mark.parametrize("missing", ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"])
def test_incomplete_smtp_configuration_does_not_send(smtp, monkeypatch, tmp_path, missing):
    config = {key: value for key, value in CONFIG.items() if key != missing}
    monkeypatch.setattr(accounts, "settings", lambda root: config)
    assert mail.send_reset(tmp_path, "recipient@example.test", link("reset")) is False
    assert smtp.events == []


def test_smtp_failure_returns_false_without_logging_credentials_or_link(
    smtp, monkeypatch, tmp_path, caplog, capsys
):
    def fail(self, message):
        raise RuntimeError(CONFIG["SMTP_PASSWORD"] + link("invite"))

    monkeypatch.setattr(smtp.cls, "send_message", fail)
    assert mail.send_invitation(tmp_path, "recipient@example.test", link("invite")) is False
    assert CONFIG["SMTP_PASSWORD"] not in caplog.text and link("invite") not in caplog.text
    output = capsys.readouterr()
    assert CONFIG["SMTP_PASSWORD"] not in output.out + output.err


def test_header_injection_in_config_is_rejected_before_smtp(smtp, monkeypatch, tmp_path):
    config = {**CONFIG, "SMTP_FROM": "mail@example.test\nBcc:attacker@example.test"}
    monkeypatch.setattr(accounts, "settings", lambda root: config)
    assert mail.send_reset(tmp_path, "recipient@example.test", link("reset")) is False
    assert smtp.events == []


def test_long_names_and_links_still_fit_under_mail_wire_limit():
    prefix = BASE + "/#/invite/"
    url = prefix + "x" * (2048 - len(prefix))
    message = mail.build_message("invite", "recipient@example.test", url, "Ա" * 10000)
    assert len(message.as_bytes()) < 100_000
    assert "Ա" * 121 not in message.get_body(preferencelist=("plain",)).get_content()
