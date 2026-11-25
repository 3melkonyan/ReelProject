"""Account email validation and multipart messages; no configuration is read at import."""
from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from html import escape
from pathlib import Path
import re
import smtplib
import ssl
from urllib.parse import urlsplit

from fastapi import HTTPException

LOGO = Path(__file__).parent / "static" / "brand" / "reel-email.png"
DNS_SECONDS = 3.0
DNS_WARNING = "Էլ․ հասցեի տիրույթի փոստային գրառումները հիմա հնարավոր չէ ստուգել։ DNS ստուգումը բաց է թողնվել։"
_TYPOS = {
    "gamil.com": "gmail.com", "gmial.com": "gmail.com", "gmai.com": "gmail.com",
    "gmal.com": "gmail.com", "gnail.com": "gmail.com", "gmail.con": "gmail.com",
    "gmail.cmo": "gmail.com", "gmail.co": "gmail.com", "gmail.cm": "gmail.com",
    "gmail.comm": "gmail.com", "hotmial.com": "hotmail.com", "hotamil.com": "hotmail.com",
    "hotmai.com": "hotmail.com", "hotmail.con": "hotmail.com", "hotmail.co": "hotmail.com", "outlok.com": "outlook.com",
    "outllok.com": "outlook.com", "outlook.con": "outlook.com", "outlook.co": "outlook.com", "yaho.com": "yahoo.com",
    "yhoo.com": "yahoo.com", "yahoo.con": "yahoo.com", "yahoo.co": "yahoo.com", "iclod.com": "icloud.com",
    "icloud.con": "icloud.com", "icloud.co": "icloud.com",
}
_CONTENT = {
    "invite": {
        "subject": "Reel․ Ձեր հաշվի հրավերը", "eyebrow": "ՀԱՇՎԻ ՀՐԱՎԵՐ",
        "title": "Բարի գալուստ Reel",
        "intro": "Ձեզ հրավիրել են միանալ Reel հարթակին։",
        "detail": "Հաշիվն ակտիվացնելու համար բացեք հրավերը և ստեղծեք Ձեր մուտքանունն ու գաղտնաբառը։",
        "cta": "Ընդունել հրավերը", "expiry": "Հղումը գործում է 72 ժամ և կարող է օգտագործվել միայն մեկ անգամ։",
        "ignore": "Եթե դուք չեք խնդրել, պարզապես անտեսեք այս նամակը։",
    },
    "reset": {
        "subject": "Reel․ գաղտնաբառի վերականգնում", "eyebrow": "ՀԱՇՎԻ ԱՆՎՏԱՆԳՈՒԹՅՈՒՆ",
        "title": "Վերականգնեք Ձեր գաղտնաբառը",
        "intro": "Ստացվել է Ձեր Reel հաշվի գաղտնաբառը վերականգնելու հարցում։",
        "detail": "Նոր գաղտնաբառ սահմանելու համար սեղմեք ստորև նշված կոճակը։",
        "cta": "Վերականգնել գաղտնաբառը", "expiry": "Հղումը գործում է 60 րոպե և կարող է օգտագործվել միայն մեկ անգամ։",
        "ignore": "Եթե դուք չեք խնդրել, պարզապես անտեսեք այս նամակը։ Ձեր գաղտնաբառը չի փոխվի։",
    },
}


def _address(email: object) -> tuple[str, str, str]:
    if not isinstance(email, str) or any(ord(c) < 32 or ord(c) == 127 for c in email):
        raise HTTPException(400, "Խնդրում ենք նշել վավեր էլ․ հասցե։")
    address = email.strip()
    if len(address) > 254 or address.count("@") != 1:
        raise HTTPException(400, "Խնդրում ենք նշել վավեր էլ․ հասցե։")
    local, raw_domain = address.rsplit("@", 1)
    try:
        domain = raw_domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise HTTPException(400, "Էլ․ հասցեի տիրույթն անվավեր է։") from None
    labels = domain.split(".")
    if (not 1 <= len(local) <= 64 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or len(labels) < 2 or len(domain) > 253 or labels[-1].isdigit()
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or len(local + "@" + domain) > 254):
        raise HTTPException(400, "Խնդրում ենք նշել վավեր էլ․ հասցե։")
    return local + "@" + domain, local, domain


def _dns():
    import dns.exception
    import dns.resolver
    return dns.resolver, dns.exception.DNSException


def validate_email(email: str) -> list[str]:
    """Reject typos/no-MX, but warn rather than block when DNS is unavailable."""
    _, local, domain = _address(email)
    if domain in _TYPOS:
        suggestion = local + "@" + _TYPOS[domain]
        raise HTTPException(400, f"Էլ․ հասցեի տիրույթում կարող է վրիպակ լինել։ Միգուցե՝ {suggestion}։")
    try:
        resolver_module, dns_error = _dns()
    except ImportError:
        return [DNS_WARNING]
    try:
        resolver = resolver_module.Resolver()
        resolver.timeout = DNS_SECONDS
        resolver.lifetime = DNS_SECONDS
        answer = resolver.resolve(domain, "MX", lifetime=DNS_SECONDS, search=False)
        records = list(answer)
    except (resolver_module.NXDOMAIN, resolver_module.NoAnswer):
        records = []
    except (dns_error, OSError, TimeoutError):
        return [DNS_WARNING]
    if not records or any(str(record.exchange) == "." for record in records):
        raise HTTPException(400, "Էլ․ հասցեի տիրույթը փոստ չի ընդունում․ MX գրառում չկա։ Խնդրում ենք ստուգել հասցեն։")
    return []


def _url(value: object) -> str:
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise ValueError("Invalid account URL")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ValueError("Invalid account URL") from None
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password
            or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"))):
        raise ValueError("Invalid account URL")
    return value


def _html(content: dict, url: str, greeting: str, theme: str) -> str:
    dark = theme == "dark"
    colors = {"background": "#101214" if dark else "#f4f5f7", "card": "#1b1e22" if dark else "#ffffff",
              "text": "#f4f5f7" if dark else "#17191d", "muted": "#b0b6c0" if dark else "#626a76",
              "border": "#343941" if dark else "#e5e8ec", "block": "#252a30" if dark else "#f4f5f7",
              "button": "#f4f5f7" if dark else "#17191d", "button_text": "#17191d" if dark else "#ffffff"}
    scheme = "light dark" if theme == "auto" else theme
    dark_css = """<style>@media (prefers-color-scheme:dark){
.mail-body{background:#101214!important}.mail-card{background:#1b1e22!important}
.mail-text{color:#f4f5f7!important}.mail-muted{color:#b0b6c0!important}
.mail-border{border-color:#343941!important}.mail-block{background:#252a30!important;border-color:#343941!important}
.mail-button{background:#f4f5f7!important;color:#17191d!important}.mail-link{color:#f4f5f7!important}}
</style>""" if theme == "auto" else ""
    c = {key: escape(value) for key, value in content.items()}
    link, hello = escape(url, quote=True), escape(greeting)
    return f"""<!doctype html>
<html lang="hy"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="{scheme}"><meta name="supported-color-schemes" content="{scheme}">{dark_css}
<title>{c['subject']}</title></head>
<body class="mail-body" style="margin:0;padding:0;background:{colors['background']};font-family:Arial,'Noto Sans Armenian',sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-body" style="background:{colors['background']};"><tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" class="mail-card mail-border" style="width:100%;max-width:600px;background:{colors['card']};border:1px solid {colors['border']};border-radius:16px;">
<tr><td bgcolor="#000000" style="padding:20px 28px;background:#000000;border-radius:16px 16px 0 0;">
<img src="cid:reel-logo" width="240" height="77" alt="Reel" style="display:block;width:240px;max-width:100%;height:auto;border:0;background:#000000;"></td></tr>
<tr><td class="mail-text" style="padding:28px;color:{colors['text']};">
<p class="mail-muted" style="margin:0 0 12px;color:{colors['muted']};font-size:11px;font-weight:bold;letter-spacing:1px;">{c['eyebrow']}</p>
<h1 class="mail-text" style="margin:0 0 24px;color:{colors['text']};font-size:26px;line-height:1.35;">{c['title']}</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.8;">{hello}</p>
<p style="margin:0 0 12px;font-size:15px;line-height:1.8;">{c['intro']}</p>
<p style="margin:0 0 24px;font-size:15px;line-height:1.8;">{c['detail']}</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="padding:0 0 22px;">
<a class="mail-button" href="{link}" style="display:inline-block;padding:14px 22px;border-radius:8px;background:{colors['button']};color:{colors['button_text']};text-decoration:none;font-size:14px;font-weight:bold;line-height:1.6;">{c['cta']}</a>
</td></tr></table>
<p class="mail-muted" style="margin:0 0 24px;color:{colors['muted']};font-size:13px;line-height:1.8;">{c['expiry']}</p>
<p class="mail-muted" style="margin:0 0 10px;color:{colors['muted']};font-size:12px;line-height:1.8;">Եթե կոճակը չի բացվում, պատճենեք այս հղումը և բացեք դիտարկչում․</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td class="mail-block mail-border" style="padding:14px;background:{colors['block']};border:1px solid {colors['border']};border-radius:8px;">
<a class="mail-link" href="{link}" style="font-family:monospace;font-size:12px;line-height:1.7;word-break:break-all;overflow-wrap:anywhere;color:{colors['text']};text-decoration:none;">{link}</a>
</td></tr></table>
<p class="mail-muted" style="margin:24px 0 0;color:{colors['muted']};font-size:13px;line-height:1.8;">{c['ignore']}</p>
</td></tr><tr><td class="mail-border" style="padding:20px 28px;border-top:1px solid {colors['border']};">
<p class="mail-text" style="margin:0 0 6px;color:{colors['text']};font-size:12px;font-weight:bold;">Reel</p>
<p class="mail-muted" style="margin:0;color:{colors['muted']};font-size:11px;line-height:1.7;">Այս նամակն ուղարկվել է ավտոմատ։ Խնդրում ենք չպատասխանել։</p>
</td></tr></table></td></tr></table></body></html>"""


def build_message(kind: str, email: str, url: str, name: str = "", *, sender: str | None = None,
                  theme: str = "auto") -> EmailMessage:
    """Build without DNS/config/network; theme light/dark is useful for previews."""
    kind = "invite" if kind == "invitation" else kind
    if kind not in _CONTENT or theme not in ("auto", "light", "dark"):
        raise ValueError("Unknown account email kind or theme")
    recipient, _, _ = _address(email)
    url = _url(url)
    clean_name = re.sub(r"[\x00-\x1f\x7f]", " ", str(name or "")).strip()[:120]
    greeting = f"Հարգելի՛ {clean_name}," if clean_name else "Բարև Ձեզ,"
    content = _CONTENT[kind]
    message = EmailMessage(policy=SMTP_POLICY)
    message["Subject"] = content["subject"]
    message["From"] = sender or "Reel <noreply@example.invalid>"
    message["To"] = recipient
    message.set_content("\n\n".join([greeting, content["intro"], content["detail"], content["cta"] + "․", url,
                                       content["expiry"], content["ignore"], "Reel"]))
    message.add_alternative(_html(content, url, greeting, theme), subtype="html")
    message.get_payload()[-1].add_related(LOGO.read_bytes(), maintype="image", subtype="png",
                                         cid="<reel-logo>", disposition="inline", filename="reel-email.png")
    return message


def _send(kind: str, root: Path, email: str, url: str, name: str) -> bool:
    # Import lazily: accounts delegates here, while configuration remains owned by accounts.
    from reel.web import accounts
    try:
        base = accounts.public_base(root)
        if not base or not isinstance(url, str) or not url.startswith(base.rstrip("/") + f"/#/{kind}/"):
            return False
        cfg = accounts.settings(root)
        if not all(cfg.get(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")):
            return False
        port = int(cfg.get("SMTP_PORT") or 587)
        if not 1 <= port <= 65535:
            return False
        message = build_message(kind, email, url, name, sender=cfg["SMTP_FROM"])
        with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(message)
        return True
    except Exception:
        # Do not log exception text, addresses, SMTP credentials or bearer links.
        return False


def send_invitation(root: Path, email: str, url: str, name: str = "") -> bool:
    return _send("invite", root, email, url, name)


def send_reset(root: Path, email: str, url: str, name: str = "") -> bool:
    return _send("reset", root, email, url, name)
