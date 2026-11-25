"""Branded Reel emails beyond account links (G-40/4): the admin sign-in code, privacy notices,
problem alerts and lesson reports.

Same look as ``reel.web.account_mail`` (black band with the CID logo, card, light/dark), a plain-text
part, one SMTP path (``SMTP_*`` in the project .env, read through ``reel.web.accounts.settings``).
Building a message needs no configuration or network. ``deliver`` is the only network seam, so tests
replace it with a fake. Addresses, codes and credentials are never logged.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import parseaddr
from html import escape
import logging
from pathlib import Path
import re
import smtplib
import ssl

log = logging.getLogger("reel.brand_mail")
LOGO = Path(__file__).parent / "web" / "static" / "brand" / "reel-email.png"
_ADDRESS = re.compile(r"[^\s@<>\"(),;:]+@[^\s@<>\"(),;:]+\.[^\s@<>\"(),;:]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(value, limit: int = 2000) -> str:
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit]


def _html(subject: str, eyebrow: str, title: str, greeting: str, paragraphs, code, code_label,
          cta, sections, note) -> str:
    e = lambda value: escape(clean(value))          # noqa: E731
    parts = [f'<p style="margin:0 0 16px;font-size:15px;line-height:1.8;">{e(greeting)}</p>' if greeting else ""]
    parts += [f'<p style="margin:0 0 14px;font-size:15px;line-height:1.8;">{e(p)}</p>' for p in paragraphs if p]
    if code:
        parts.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" '
            'class="mail-block" style="padding:22px 12px;margin:8px 0;background:#f4f5f7;border:2px solid #D500C8;border-radius:12px;">'
            f'<p class="mail-muted" style="margin:0 0 8px;color:#626a76;font-size:12px;font-weight:bold;letter-spacing:1px;">{e(code_label)}</p>'
            f'<p class="mail-text" style="margin:0;font-family:Menlo,Consolas,monospace;font-size:34px;font-weight:bold;letter-spacing:10px;color:#17191d;">{e(code)}</p>'
            '</td></tr></table><p style="margin:0 0 8px;"></p>')
    if cta:
        label, url = cta
        link = escape(clean(url), quote=True)
        parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="padding:6px 0 18px;">'
            f'<a class="mail-button" href="{link}" style="display:inline-block;padding:14px 22px;border-radius:8px;background:#17191d;'
            f'color:#ffffff;text-decoration:none;font-size:14px;font-weight:bold;line-height:1.6;">{e(label)}</a></td></tr></table>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td class="mail-block" '
            'style="padding:12px;background:#f4f5f7;border:1px solid #e5e8ec;border-radius:8px;">'
            f'<a class="mail-link" href="{link}" style="font-family:monospace;font-size:12px;line-height:1.7;word-break:break-all;color:#17191d;text-decoration:none;">{link}</a>'
            '</td></tr></table><p style="margin:0 0 8px;"></p>')
    for heading, lines in sections:
        items = "".join(f'<li style="margin:0 0 6px;">{e(line)}</li>' for line in lines if line)
        if items:
            parts.append(f'<h2 class="mail-text" style="margin:22px 0 8px;font-size:17px;color:#17191d;">{e(heading)}</h2>'
                         f'<ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.7;">{items}</ul>')
    if note:
        parts.append(f'<p class="mail-muted" style="margin:22px 0 0;color:#626a76;font-size:13px;line-height:1.8;">{e(note)}</p>')
    return f"""<!doctype html>
<html lang="hy"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<style>@media (prefers-color-scheme:dark){{.mail-body{{background:#101214!important}}.mail-card{{background:#1b1e22!important;border-color:#343941!important}}
.mail-text{{color:#f4f5f7!important}}.mail-muted{{color:#b0b6c0!important}}.mail-block{{background:#252a30!important}}
.mail-button{{background:#f4f5f7!important;color:#17191d!important}}.mail-link{{color:#f4f5f7!important}}}}</style>
<title>{e(subject)}</title></head>
<body class="mail-body" style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,'Noto Sans Armenian',sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-body" style="background:#f4f5f7;"><tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" class="mail-card" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e5e8ec;border-radius:16px;">
<tr><td bgcolor="#000000" style="padding:20px 28px;background:#000000;border-radius:16px 16px 0 0;">
<img src="cid:reel-logo" width="240" height="77" alt="Reel" style="display:block;width:240px;max-width:100%;height:auto;border:0;background:#000000;"></td></tr>
<tr><td class="mail-text" style="padding:28px;color:#17191d;">
<p class="mail-muted" style="margin:0 0 12px;color:#626a76;font-size:11px;font-weight:bold;letter-spacing:1px;">{e(eyebrow)}</p>
<h1 class="mail-text" style="margin:0 0 22px;color:#17191d;font-size:24px;line-height:1.35;">{e(title)}</h1>
{''.join(parts)}
</td></tr><tr><td style="padding:20px 28px;border-top:1px solid #e5e8ec;">
<p class="mail-text" style="margin:0 0 6px;color:#17191d;font-size:12px;font-weight:bold;">Reel</p>
<p class="mail-muted" style="margin:0;color:#626a76;font-size:11px;line-height:1.7;">Այս նամակն ուղարկվել է ավտոմատ։ Խնդրում ենք չպատասխանել։</p>
</td></tr></table></td></tr></table></body></html>"""


def build(to: str, subject: str, *, eyebrow: str, title: str, greeting: str = "", paragraphs=(),
          code: str | None = None, code_label: str = "", cta: tuple[str, str] | None = None, sections=(),
          note: str = "", sender: str | None = None) -> EmailMessage:
    """A multipart message (plain text + HTML + inline logo); no configuration or network."""
    to = clean(to, 254)
    if not _ADDRESS.fullmatch(to):
        raise ValueError("invalid recipient")
    if cta and not re.match(r"https://|http://(127\.0\.0\.1|localhost)[:/]", str(cta[1])):
        raise ValueError("email links must be https")
    paragraphs, sections = [p for p in paragraphs if p], [(h, [x for x in lines if x]) for h, lines in sections]
    message = EmailMessage(policy=SMTP_POLICY)
    message["Subject"] = clean(subject, 200)
    message["From"] = sender or "Reel <noreply@example.invalid>"
    message["To"] = to
    text = [clean(greeting)] if greeting else []
    text += [clean(p) for p in paragraphs]
    if code:
        text.append(f"{clean(code_label)}: {clean(code)}")
    if cta:
        text.append(f"{clean(cta[0])}: {clean(cta[1])}")
    for heading, lines in sections:
        if lines:
            text.append(clean(heading) + "\n" + "\n".join("• " + clean(line) for line in lines))
    if note:
        text.append(clean(note))
    text.append("Reel")
    message.set_content("\n\n".join(x for x in text if x))
    message.add_alternative(_html(subject, eyebrow, title, greeting, paragraphs, code, code_label, cta, sections, note),
                            subtype="html")
    message.get_payload()[-1].add_related(LOGO.read_bytes(), maintype="image", subtype="png",
                                         cid="<reel-logo>", disposition="inline", filename="reel-email.png")
    return message


def settings(root) -> dict:
    from reel.web import accounts
    return accounts.settings(root)


def configured(root) -> bool:
    try:
        cfg = settings(root)
    except Exception:  # noqa: BLE001
        return False
    return all(cfg.get(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))


def deliver(root, message: EmailMessage) -> bool:
    """Send through the configured SMTP server (STARTTLS). False when SMTP is not configured or fails."""
    try:
        cfg = settings(root)
        if not all(cfg.get(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")):
            return False
        port = int(cfg.get("SMTP_PORT") or 587)
        if not 1 <= port <= 65535:
            return False
        message.replace_header("From", cfg["SMTP_FROM"])
        with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 - never leak addresses, codes or credentials
        log.warning("email delivery failed (%s)", type(exc).__name__)
        return False


def send(root, to: str, subject: str, **content) -> bool:
    try:
        message = build(to, subject, **content)
    except (ValueError, OSError) as exc:
        log.warning("email not built (%s)", type(exc).__name__)
        return False
    return deliver(root, message)


def sender_address(root) -> str | None:
    """The bare address of SMTP_FROM, for the privacy page contact line."""
    try:
        address = parseaddr(str(settings(root).get("SMTP_FROM") or ""))[1]
    except Exception:  # noqa: BLE001
        return None
    return address if _ADDRESS.fullmatch(address or "") else None


def admin_emails(conn) -> list[str]:
    """Active personal admins (removed and deleted accounts excluded)."""
    try:
        rows = conn.execute("SELECT email FROM users WHERE role='admin' AND active=1 AND removed_at IS NULL "
                            "AND deleted_at IS NULL ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 - a database without accounts yet
        return []
    return [row[0] for row in rows if row[0] and _ADDRESS.fullmatch(row[0])]
