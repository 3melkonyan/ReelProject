"""Personal identities on the existing signed session. Never stores bearer tokens or passwords."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from reel.db import email_identity
from reel.i18n import t as _t

PRINCIPAL = ContextVar("reel_principal", default=None)
AUTH_ERROR_KEY = "accounts.incorrect_username_or_password"
TOKEN_ERROR_KEY = "accounts.this_link_is_invalid_or_has_expired"


def utc():
    return datetime.now(UTC).isoformat(timespec="seconds")


def groups():
    p = PRINCIPAL.get()
    return None if not p or p.get("role") == "admin" or p.get("legacy") else frozenset(p["groups"])


def allowed(group):
    return groups() is None or group in groups()


def require_group(group):
    if not allowed(group):
        raise HTTPException(404, _t("access.not_found"))


def hash_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 1024:
        raise HTTPException(400, _t("accounts.the_password_must_contain_10_1024_characters"))
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if (
            kind != "scrypt"
            or (int(n), int(r), int(p)) != (16384, 8, 1)
            or len(str(password)) > 1024
        ):
            return False
        got = hashlib.scrypt(
            str(password).encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(got, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def identity(value):
    if not isinstance(value, str):
        raise HTTPException(400, _t("accounts.invalid_username"))
    value = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", value):
        raise HTTPException(400, _t("accounts.use_3_64_latin_letters_digits_periods"))
    return value


def fields(name, email, role, selected, known):
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 120
        or any(c in name for c in "\r\n")
    ):
        raise HTTPException(400, _t("account_api.enter_a_name_of_up_to_120"))
    if (
        not isinstance(email, str)
        or len(email) > 254
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip())
    ):
        raise HTTPException(400, _t("accounts.enter_a_valid_email_address"))
    if (
        role not in ("admin", "lecturer", "student")
        or not isinstance(selected, list)
        or any(not isinstance(g, str) for g in selected)
    ):
        raise HTTPException(400, _t("accounts.choose_a_role_and_groups"))
    selected = list(dict.fromkeys(selected))
    if role == "admin":
        selected = []
    elif (
        (role == "student" and len(selected) != 1)
        or (role == "lecturer" and not selected)
        or any(g not in known for g in selected)
    ):
        raise HTTPException(400, _t("accounts.students_need_one_group_lecturers_need_at"))
    return dict(name=name.strip(), email=email_identity(email), role=role, groups=selected)


def public_user(row):
    columns = set(row.keys())
    value = {
        k: row[k]
        for k in ("id", "login", "email", "name", "role", "active", "created_at", "last_login_at")
    }
    removed = row["removed_at"] if "removed_at" in columns else None
    deleted = row["deleted_at"] if "deleted_at" in columns else None
    return value | {
        "groups": json.loads(row["groups_json"]),
        "removed_at": removed,
        "deleted_at": deleted,
        "removed": bool(removed),
        "deleted": bool(deleted),
        "avatar": f"/api/accounts/avatars/{row['id']}?v={row['avatar']}"
        if row["avatar"] and not deleted
        else None,
    }


def legacy_enabled(db):
    row = db.conn.execute(
        "SELECT value FROM account_settings WHERE key='legacy_enabled'"
    ).fetchone()
    return not row or row[0] == "true"


def find_user(db, login):
    value = str(login).strip().casefold()
    return db.conn.execute(
        "SELECT * FROM users WHERE login=? OR email_identity(email)=? ORDER BY id LIMIT 1",
        (value, email_identity(value)),
    ).fetchone()


def create_user(db, login, password, payload):
    login = identity(login)
    hashed = hash_password(password)
    email = email_identity(payload["email"])
    owns_transaction = not db.conn.in_transaction
    if owns_transaction:
        db.conn.execute("BEGIN IMMEDIATE")
    try:
        # A UNIQUE expression index would prevent opening legacy databases with aliases.
        if find_user(db, email):
            raise sqlite3.IntegrityError("email already exists")
        cur = db.conn.execute(
            "INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                login,
                email,
                payload["name"],
                hashed,
                payload["role"],
                json.dumps(payload["groups"]),
                utc(),
            ),
        )
        if owns_transaction:
            db.conn.execute("COMMIT")
        return cur.lastrowid
    except BaseException:
        if owns_transaction and db.conn.in_transaction:
            db.conn.execute("ROLLBACK")
        raise


def session_user(request, row, db=None):
    if db is not None:
        stamp = utc()
        db.conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (stamp, row["id"]))
        row = dict(row)
        row["last_login_at"] = stamp
    request.session.clear()
    request.session.update(
        user_id=row["id"], session_version=row["session_version"], role=row["role"]
    )
    return row


def issue_token(db, kind, payload=None, user_id=None, actor=None):
    if isinstance(payload, dict) and isinstance(payload.get("email"), str):
        payload = {**payload, "email": email_identity(payload["email"])}
    raw = secrets.token_urlsafe(32)
    now = time.time()
    cur = db.conn.execute(
        "INSERT INTO account_tokens(digest,kind,user_id,payload,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
        (
            hashlib.sha256(raw.encode()).hexdigest(),
            kind,
            user_id,
            json.dumps(payload or {}, ensure_ascii=False),
            actor,
            now,
            now + (72 * 3600 if kind == "invite" else 3600),
        ),
    )
    return cur.lastrowid, raw


def token_row(db, raw, kind):
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        raise HTTPException(400, _t(TOKEN_ERROR_KEY))
    row = db.conn.execute(
        "SELECT * FROM account_tokens WHERE digest=? AND kind=?",
        (hashlib.sha256(raw.encode()).hexdigest(), kind),
    ).fetchone()
    if (
        not row
        or row["used_at"] is not None
        or row["revoked_at"] is not None
        or row["expires_at"] <= time.time()
    ):
        raise HTTPException(400, _t(TOKEN_ERROR_KEY))
    return row


def redeem(db, raw, kind, *, login=None, password=None, known=(), consent=None):
    # Reject invalid bearer tokens before expensive work; recheck under the write lock.
    token_row(db, raw, kind)
    if consent is not None:
        from reel.web import privacy

        privacy.ensure(db.conn)
    # Expensive scrypt is computed before acquiring the write lock.
    hashed = hash_password(password)
    clean_login = identity(login) if kind == "invite" else None
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        token = token_row(db, raw, kind)
        payload = json.loads(token["payload"])
        if kind == "invite":
            payload = fields(
                payload["name"], payload["email"], payload["role"], payload["groups"], known
            )
            from reel.web.group_status import completed_groups

            done = completed_groups(db)
            if any(group in done for group in payload["groups"]):
                raise HTTPException(
                    400, _t("accounts.the_invitation_s_group_has_completed_contact")
                )
            if find_user(db, payload["email"]):
                raise sqlite3.IntegrityError("email already exists")
        cur = db.conn.execute(
            "UPDATE account_tokens SET used_at=? WHERE id=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at>?",
            (time.time(), token["id"], time.time()),
        )
        if cur.rowcount != 1:
            raise HTTPException(400, _t(TOKEN_ERROR_KEY))
        if kind == "invite":
            user_id = db.conn.execute(
                "INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    clean_login,
                    payload["email"],
                    payload["name"],
                    hashed,
                    payload["role"],
                    json.dumps(payload["groups"]),
                    utc(),
                ),
            ).lastrowid
            if consent is not None:
                privacy.record(db.conn, user_id, consent)
            db.conn.execute(
                "UPDATE account_tokens SET registered_user_id=? WHERE id=?", (user_id, token["id"])
            )
        else:
            user_id = token["user_id"]
            cur = db.conn.execute(
                "UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=? AND active=1",
                (hashed, user_id),
            )
            if cur.rowcount != 1:
                raise HTTPException(400, _t(TOKEN_ERROR_KEY))
            db.conn.execute(
                "UPDATE account_tokens SET revoked_at=? WHERE user_id=? AND kind='reset' AND used_at IS NULL",
                (time.time(), user_id),
            )
        db.conn.execute("COMMIT")
        return user_id
    except sqlite3.IntegrityError:
        db.conn.execute("ROLLBACK")
        raise HTTPException(409, _t("accounts.this_username_or_email_address_is_already")) from None
    except BaseException:
        db.conn.execute("ROLLBACK")
        raise


def login_limit(db, login, ip, *, failed=None):
    key = hashlib.sha256((email_identity(str(login)) + "\0" + ip).encode()).hexdigest()
    now = time.time()
    row = db.conn.execute("SELECT * FROM account_limits WHERE key=?", (key,)).fetchone()
    if failed is None:
        if row and row["locked_until"] > now:
            raise HTTPException(429, _t("accounts.too_many_attempts_try_again_in_15"))
    elif failed:
        count = (
            row["failures"]
            if row and (row["locked_until"] == 0 or row["locked_until"] > now)
            else 0
        ) + 1
        db.conn.execute(
            "INSERT INTO account_limits VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until",
            (key, count, now + 900 if count >= 5 else 0),
        )
    else:
        db.conn.execute("DELETE FROM account_limits WHERE key=?", (key,))


def settings(root):
    from dotenv import dotenv_values

    values = {**dotenv_values(Path(root) / ".env", interpolate=False), **os.environ}
    return values


def public_base(root):
    values = settings(root)
    url = str(values.get("REEL_PUBLIC_URL") or "").strip()
    if not url:
        try:
            url = (Path(root) / "logs" / "tunnel_url.txt").read_text().strip()
        except OSError:
            return None
        if not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", url):
            return None
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Validate the lazily parsed port.
    except ValueError:
        return None
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(c in url for c in "\r\n")
        or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"))
    ):
        return None
    return url.rstrip("/")


def link(root, kind, raw):
    return (public_base(root) or "") + f"/#/{kind}/{raw}"


def send_reset(root, email, url):
    # Keep the established monkeypatch seam while sharing branded delivery.
    from reel.web import account_mail

    return account_mail.send_reset(root, email, url)


# Same scrypt cost for unknown identities; this is not a usable account credential.
DUMMY_HASH = hash_password("reel-non-account-timing-value")


DELETED_USER_LABEL_KEY = "accounts.deleted_user"


def revoke_user_tokens(db, row, *, anonymize=False):
    """Invalidate reset links and invitations, including invitations bound only by email."""
    matches = (
        "user_id=? OR registered_user_id=? OR email_identity(json_extract(payload,'$.email'))=?"
    )
    values = (row["id"], row["id"], email_identity(row["email"]))
    db.conn.execute(
        f"UPDATE account_tokens SET revoked_at=coalesce(revoked_at,?) WHERE {matches}",
        (time.time(), *values),
    )
    if anonymize:
        # Do not rewrite invitations created BY this administrator for other people.
        payload = json.dumps(
            {
                "name": _t(DELETED_USER_LABEL_KEY),
                "email": "",
                "role": "student",
                "groups": [],
                "anonymized": True,
            },
            ensure_ascii=False,
        )
        db.conn.execute(f"UPDATE account_tokens SET payload=? WHERE {matches}", (payload, *values))


def protect_admin(db, row, actor, *, disabling=True):
    if disabling and row["id"] == actor:
        raise HTTPException(409, _t("accounts.you_cannot_remove_or_deactivate_your_own"))
    if (
        disabling
        and row["role"] == "admin"
        and row["active"]
        and not row["removed_at"]
        and not row["deleted_at"]
    ):
        count = db.conn.execute(
            "SELECT count(*) FROM users WHERE role='admin' AND active=1 AND removed_at IS NULL AND deleted_at IS NULL"
        ).fetchone()[0]
        if count <= 1:
            raise HTTPException(409, _t("accounts.you_cannot_remove_deactivate_or_change_the"))


def invitation_status(row, now=None):
    now = time.time() if now is None else now
    return (
        "revoked"
        if row["revoked_at"] is not None
        else "used"
        if row["used_at"] is not None
        else "expired"
        if row["expires_at"] <= now
        else "pending"
    )


def public_invitation(row):
    payload = json.loads(row["payload"])
    return {
        key: payload.get(key, [] if key == "groups" else "")
        for key in ("name", "email", "role", "groups")
    } | {
        "id": row["id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "used_at": row["used_at"],
        "revoked_at": row["revoked_at"],
        "registered_login": row["registered_login"],
        "status": invitation_status(row),
    }


def directory(db, known, query, completed=frozenset()):
    """Filter and page in SQLite, with Python casefold for Armenian and Latin alike."""
    db.conn.create_function(
        "reel_surname",
        1,
        lambda value: str(value or "").strip().split()[-1].casefold()
        if str(value or "").strip()
        else "",
    )
    tab = query.get("tab", "users")
    role = query.get("role", "")
    status = query.get("status", "")
    if tab in ("student", "lecturer", "admin"):
        role, tab = tab, "users"
    scope = query.get("group_status", query.get("group_scope", "active"))
    if (
        tab not in ("users", "removed", "invitations")
        or role not in ("", "admin", "lecturer", "student")
        or scope not in ("active", "completed", "all")
    ):
        raise HTTPException(400, _t("accounts.choose_valid_list_role_and_group_filters"))
    try:
        page = int(query.get("page", 1))
        size = int(query.get("page_size", 20))
        if not 1 <= page <= 1_000_000_000 or not 1 <= size <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, _t("accounts.choose_a_valid_page_and_a_page")) from None
    search = query.get("q", "").strip().casefold()
    group = query.get("group", "")
    if len(search) > 254 or len(group) > 200:
        raise HTTPException(400, _t("accounts.the_search_text_is_too_long"))
    totals = db.conn.execute(
        "SELECT role,count(*) n FROM users WHERE removed_at IS NULL AND deleted_at IS NULL GROUP BY role"
    ).fetchall()
    counts = {role: 0 for role in ("student", "lecturer", "admin")}
    counts.update({row["role"]: row["n"] for row in totals})
    counts["users"] = sum(counts.values())
    counts["removed"] = db.conn.execute(
        "SELECT count(*) FROM users WHERE removed_at IS NOT NULL AND deleted_at IS NULL"
    ).fetchone()[0]
    counts["invitations"] = db.conn.execute(
        "SELECT count(*) FROM account_tokens WHERE kind='invite'"
    ).fetchone()[0]
    counts.update(students=counts["student"], lecturers=counts["lecturer"], admins=counts["admin"])
    where = []
    args = []
    if tab == "invitations":
        table = "account_tokens t LEFT JOIN users u ON u.id=t.registered_user_id"
        select = "t.*,u.login registered_login"
        order = "t.id DESC"
        where = ["t.kind='invite'"]
        name = "json_extract(t.payload,'$.name')"
        email = "json_extract(t.payload,'$.email')"
        role_field = "json_extract(t.payload,'$.role')"
        groups_field = "json_extract(t.payload,'$.groups')"
        if status:
            if status not in ("pending", "used", "expired", "revoked"):
                raise HTTPException(400, _t("accounts.choose_a_valid_invitation_status"))
            where.append(
                "(CASE WHEN t.revoked_at IS NOT NULL THEN 'revoked' WHEN t.used_at IS NOT NULL THEN 'used' WHEN t.expires_at<=? THEN 'expired' ELSE 'pending' END)=?"
            )
            args.extend((time.time(), status))
    else:
        table = "users"
        select = "*"
        order = "reel_surname(name),casefold(name),id"
        where = [
            "deleted_at IS NULL",
            "removed_at IS NOT NULL" if tab == "removed" else "removed_at IS NULL",
        ]
        name = "name"
        email = "email"
        role_field = "role"
        groups_field = "groups_json"
        if status:
            if status not in ("active", "inactive"):
                raise HTTPException(400, _t("accounts.choose_a_valid_account_status"))
            where.append("active=?")
            args.append(int(status == "active"))
    if role:
        where.append(role_field + "=?")
        args.append(role)
    if group:
        where.append(f"EXISTS (SELECT 1 FROM json_each({groups_field}) WHERE value=?)")
        args.append(group)
    if search:
        where.append(
            f"(instr(casefold({name}),?)>0 OR instr(casefold({email}),?)>0 OR instr(email_identity({email}),?)>0)"
        )
        args.extend((search, search, email_identity(search)))
    condition = " AND ".join(where)
    total = db.conn.execute(f"SELECT count(*) FROM {table} WHERE {condition}", args).fetchone()[0]
    rows = db.conn.execute(
        f"SELECT {select} FROM {table} WHERE {condition} ORDER BY {order} LIMIT ? OFFSET ?",
        (*args, size, (page - 1) * size),
    ).fetchall()
    choices = sorted(
        (name for name in known if scope == "all" or (name in completed) == (scope == "completed")),
        key=str.casefold,
    )
    return {
        "items": [
            public_invitation(row) if tab == "invitations" else public_user(row) for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": size,
        "pages": max(1, (total + size - 1) // size),
        "counts": counts,
        "groups": choices,
        "group_status": scope,
    }
