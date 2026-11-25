"""Explicit, reversible G-40 demo setup. Never runs a worker or contacts a service.

Use --apply or --remove with --root. The default root is this checkout, but tests
must always supply temporary roots. Course dates/people are illustrative; official
course duration sources are frozen in demo_courses.json. Passwords are written
only to the mode-0600 out/demo/demo-accounts.json file, never printed.
"""
from __future__ import annotations

import argparse
import base64
import calendar
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

TAG = "g40-demo-v1"
REAL_GROUP = "AI 2.6"
OLD_GROUP = "Group-A"
PLAYLIST = "PLHrVtbz7Bx1w"
SLACK = "C0000000000"
CREDENTIALS = Path("out/demo/demo-accounts.json")
FIRST = ["Անի", "Արամ", "Մարիամ", "Հայկ", "Լիլիթ", "Նարեկ", "Սոնա", "Դավիթ", "Աննա", "Գոռ",
         "Մանե", "Տիգրան", "Նարինե", "Վահե", "Էլեն", "Աշոտ", "Լուսինե", "Սամվել", "Արփի", "Արթուր"]
LAST = ["Սարգսյան", "Հակոբյան", "Գրիգորյան", "Հարությունյան", "Մարտիրոսյան", "Մկրտչյան", "Ավետիսյան",
        "Դավթյան", "Գևորգյան", "Մանուկյան", "Պետրոսյան", "Մարգարյան", "Ստեփանյան", "Վարդանյան",
        "Սահակյան", "Հովհաննիսյան", "Խաչատրյան", "Սիմոնյան", "Մինասյան", "Մելքոնյան"]


class DemoConflict(ValueError):
    """A safe conflict description: never contains a password or bearer link."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _months(day, count):
    month = day.year * 12 + day.month - 1 + count
    year, zero_month = divmod(month, 12)
    return date(year, zero_month + 1, min(day.day, calendar.monthrange(year, zero_month + 1)[1]))


def _root(root):
    root = Path(root).expanduser()
    if ".." in root.parts:
        raise DemoConflict("Root must not contain traversal components")
    root = root.absolute()
    if not root.is_dir() or root.is_symlink():
        raise DemoConflict("Root must be an existing nonsymlink directory")
    for relative in ("config.yaml", "config.yaml.bak", "reel.db", "out", "out/demo", str(CREDENTIALS)):
        if (root / relative).is_symlink():
            raise DemoConflict("A managed path is a symlink: " + relative)
    return root


def _atomic(path, content, mode=0o600, *, exclusive=False):
    if path.is_symlink():
        raise DemoConflict("A managed file is a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".g40-demo-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path, follow_symlinks=False)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _database(root):
    from reel.db import DB
    database = DB(root / "reel.db")
    try:
        yield database
    finally:
        database.conn.close()


def _registry(database, kind, key, payload):
    database.conn.execute("INSERT INTO demo_registry(kind,key,owner_tag,payload_json) VALUES(?,?,?,?)",
                          (kind, str(key), TAG, _json({"demo_tag": TAG, **payload})))


def _records(database, kind=None):
    query = "SELECT * FROM demo_registry WHERE owner_tag=?"
    parameters = [TAG]
    if kind is not None:
        query += " AND kind=?"
        parameters.append(kind)
    return [(row["kind"], row["key"], json.loads(row["payload_json"]))
            for row in database.conn.execute(query, parameters)]


def _forget(database, kind, key):
    database.conn.execute("DELETE FROM demo_registry WHERE owner_tag=? AND kind=? AND key=?", (TAG, kind, str(key)))


def _row(database, table, key, value):
    row = database.conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
    return dict(row) if row else None


def _logical_meta(row):
    return {key: value for key, value in (row or {}).items() if key != "updated_at"}


def _restore_meta(database, name, row):
    database.conn.execute("DELETE FROM group_metadata WHERE name=?", (name,))
    if row:
        columns = list(row)
        database.conn.execute("INSERT INTO group_metadata(" + ",".join(columns) + ") VALUES(" +
                              ",".join("?" for _ in columns) + ")", [row[key] for key in columns])


def course_snapshot():
    return json.loads(Path(__file__).with_name("demo_courses.json").read_text(encoding="utf-8"))


def _cohorts(today):
    # Cohort dates stay fixed; the reference date never moves the schedule.
    result = []
    for course in course_snapshot()["courses"]:
        periods = [("2.6", date(2026, 10, 1)), ("2.5", date(2026, 6, 1))]
        if course["id"] in ("ai-modern", "ai-junior", "ai-uxui"):
            periods.append(("2.4", date(2026, 2, 1)))
        prefix = " ".join(course["name"].translate(str.maketrans({"/": " ", "(": " ", ")": " "})).split())
        for suffix, start in periods:
            end = _months(start, course["duration_months"]) - timedelta(days=1)
            name = REAL_GROUP if course["id"] == "ai-modern" and suffix == "2.6" else prefix + " " + suffix
            result.append({"name": name, "course": course, "start": start, "end": end,
                           "key": suffix.replace(".", "-")})
    return result


def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"



def _user_fingerprint(row):
    fields = {key: row[key] for key in ("login", "email", "name", "password_hash", "role", "groups_json", "avatar",
                                       "active", "session_version", "removed_at", "deleted_at", "demo_tag")}
    # Formatting/order-only group rewrites are not a change of membership.
    try:
        groups = json.loads(fields["groups_json"])
        if isinstance(groups, list) and all(isinstance(group, str) for group in groups):
            fields["groups_json"] = sorted(set(groups))
    except (TypeError, ValueError):
        pass
    return _sha(_json(fields).encode())


def _user(database, login, name, role, groups, password_hash="!demo-unusable"):
    first,last=(name.split()+['Demo','Person'])[:2]
    first_latin=['ani','aram','mariam','hayk','lilit','narek','sona','davit','anna','gor','mane','tigran','narine','vahe','elen','ashot','lusine','samvel','arpi','artur']
    last_latin=['sargsyan','hakobyan','grigoryan','harutyunyan','martirosyan','mkrtchyan','avetisyan','davtyan','gevorgyan','manukyan','petrosyan','margaryan','stepanyan','vardanyan','sahakyan','hovhannisyan','khachatryan','simonyan','minasyan','melkonyan']
    f=first_latin[FIRST.index(first)] if first in FIRST else 'demo'
    l=last_latin[LAST.index(last)] if last in LAST else 'demoyan'
    email=f+'.'+l+'@example.com';suffix=1
    while database.conn.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
        suffix+=1;email=f+'.'+l+str(suffix)+'@example.com'
    if database.conn.execute("SELECT 1 FROM users WHERE lower(login)=lower(?) OR lower(email)=lower(?)", (login, email)).fetchone():
        raise DemoConflict("Refusing to overwrite an existing account: " + login)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ident = database.conn.execute("INSERT INTO users(login,email,name,password_hash,role,groups_json,created_at,demo_tag) "
                                  "VALUES(?,?,?,?,?,?,?,?)", (login, email, name, password_hash, role, _json(groups), created, TAG)).lastrowid
    _registry(database, "user", ident, {"login": login, "fingerprint": _user_fingerprint(_row(database, "users", "id", ident))})
    return ident


def _schedule(cohort, group, lecturer, tas):
    start, end = cohort["start"], cohort["end"]
    days = (end - start).days + 1
    subjects = group["subjects"]
    return {"course_start": start.isoformat(), "course_end": end.isoformat(), "ta_ids": tas,
            "subjects": [{"name": subject["name"], "lecturer_id": lecturer,
                          "start_date": (start + timedelta(days=days * i // len(subjects))).isoformat(),
                          "end_date": (start + timedelta(days=days * (i + 1) // len(subjects) - 1)).isoformat()}
                         for i, subject in enumerate(subjects)]}


def _backup(path):
    return {"data": base64.b64encode(path.read_bytes()).decode(), "mode": stat.S_IMODE(path.stat().st_mode)} if path.exists() else None


def _restore_backup(path, saved):
    if saved is None:
        path.unlink(missing_ok=True)
    else:
        _atomic(path, base64.b64decode(saved["data"]), saved["mode"])


def _save_config(root, config):
    from reel.config import save_config
    save_config(config, str(root / "config.yaml"))


def apply(root=PROJECT, *, today=None):
    """Create only explicitly tagged demo rows; the Group-A mapping is reversible."""
    from reel.config import load_config
    from reel.web.group_management import save_details
    root = _root(root)
    today = today or date.today()
    config = load_config(str(root / "config.yaml"))
    original_config = (root / "config.yaml").read_bytes()
    original_mode = stat.S_IMODE((root / "config.yaml").stat().st_mode)
    backup_before = _backup(root / "config.yaml.bak")
    credentials = root / CREDENTIALS
    config_written = credentials_written = False
    with _database(root) as database:
        database.conn.execute("BEGIN IMMEDIATE")
        try:
            if _records(database, "run"):
                database.conn.rollback()
                return {"status": "already_applied", "demo_tag": TAG, "version": _records(database,"run")[0][2].get("version",1), "upgrade_required":_records(database,"run")[0][2].get("version",1)<2}
            if _records(database) or credentials.exists():
                raise DemoConflict("Unregistered demo records or credential file already exist")
            cohorts = _cohorts(today)
            existing = config["groups"]
            for cohort in cohorts:
                if cohort["name"] in existing:
                    raise DemoConflict("Refusing to overwrite an existing group: " + cohort["name"])
                if _row(database, "group_metadata", "name", cohort["name"]):
                    raise DemoConflict("Existing group metadata conflicts: " + cohort["name"])
            replacing = OLD_GROUP in existing
            if replacing and database.conn.execute("SELECT 1 FROM jobs WHERE grp=? AND status IN ('queued','running')", (OLD_GROUP,)).fetchone():
                raise DemoConflict("Group-A has queued/running jobs; leave it untouched")
            old_group = existing.pop(OLD_GROUP) if replacing else None
            old_meta = _row(database, "group_metadata", "name", OLD_GROUP) if replacing else None
            if replacing:
                for row in database.conn.execute("SELECT id,groups_json FROM users WHERE coalesce(demo_tag,'')!=?", (TAG,)).fetchall():
                    groups = json.loads(row["groups_json"])
                    if OLD_GROUP in groups:
                        changed = list(dict.fromkeys(REAL_GROUP if value == OLD_GROUP else value for value in groups))
                        database.conn.execute("UPDATE users SET groups_json=?,session_version=session_version+1 WHERE id=?", (_json(changed), row["id"]))
                        _registry(database, "membership", row["id"], {"before": groups, "after": changed})
                for row in database.conn.execute("SELECT id FROM jobs WHERE grp=?", (OLD_GROUP,)).fetchall():
                    database.conn.execute("UPDATE jobs SET grp=? WHERE id=?", (REAL_GROUP, row["id"]))
                    _registry(database, "job_group", row["id"], {"before": OLD_GROUP, "after": REAL_GROUP})
                database.conn.execute("DELETE FROM group_metadata WHERE name=?", (OLD_GROUP,))
            for row in database.conn.execute('SELECT id,subject FROM jobs WHERE grp=? AND subject=?',(REAL_GROUP,OLD_GROUP)).fetchall():
                database.conn.execute('UPDATE jobs SET subject=? WHERE id=?',('Python',row['id']))
                _registry(database,'job_subject',row['id'],{'before':OLD_GROUP,'after':'Python'})
            password = secrets.token_urlsafe(18)
            lecturer_ids = {}
            for index, course in enumerate(course_snapshot()["courses"]):
                group_names = [cohort["name"] for cohort in cohorts if cohort["course"]["id"] == course["id"]]
                login = "demo-lecturer" if course["id"] == "ai-modern" else "demo-g40-lecturer-" + course["id"]
                lecturer_ids[course["id"]] = _user(database, login, FIRST[index] + " " + LAST[index], "lecturer", group_names,
                                                _hash_password(password) if login == "demo-lecturer" else "!demo-unusable")
            student = _user(database, "demo-student", "Անի Դեմոյան", "student", [REAL_GROUP], _hash_password(password))
            assistant = _user(database, "demo-ta", "Նարեկ Դեմոյան", "student", [REAL_GROUP], _hash_password(password))
            before_settings = {row["key"]: row["value"] for row in database.conn.execute("SELECT * FROM account_settings")}
            audit_start = database.conn.execute("SELECT coalesce(max(id),0) FROM audit_events").fetchone()[0]
            for index, cohort in enumerate(cohorts):
                name, course = cohort["name"], cohort["course"]
                migrated = replacing and name == REAL_GROUP
                group = json.loads(_json(old_group)) if migrated else {"subjects": [{"name": label, "playlist": ""} for label in course["subject_labels"]],
                                                                       "slack_channel": "DEMO-NOT-CONNECTED", "lecturers": []}
                if name == REAL_GROUP:
                    group["slack_channel"] = SLACK
                    for subject in group['subjects']:
                        if subject['name']==OLD_GROUP:subject['name']='Python'
                    if not any(subject.get("playlist") for subject in group["subjects"]):
                        group["subjects"][0]["playlist"] = PLAYLIST
                    known_subjects = {subject["name"].casefold() for subject in group["subjects"]}
                    for label in course["subject_labels"]:
                        if len(group["subjects"]) < 5 and label.casefold() not in known_subjects:
                            group["subjects"].append({"name": label, "playlist": ""})
                            known_subjects.add(label.casefold())
                existing[name] = group
                tas = [assistant] if name == REAL_GROUP else []
                desired_tas = 1 + index % 2
                for ta_index in range(desired_tas - len(tas)):
                    login = f"demo-g40-{course['id']}-{cohort['key']}-ta{ta_index + 1}"
                    tas.append(_user(database, login, FIRST[(index + ta_index + 4) % len(FIRST)] + " " + LAST[(index + ta_index + 7) % len(LAST)], "student", [name]))
                count = database.conn.execute("SELECT count(*) FROM users WHERE role='student' AND active=1 AND removed_at IS NULL AND deleted_at IS NULL "
                                              "AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (name,)).fetchone()[0]
                for number in range(max(0, 18 - count)):
                    login = f"demo-g40-{course['id']}-{cohort['key']}-s{number + 1:02d}"
                    _user(database, login, FIRST[(number + index) % len(FIRST)] + " " + LAST[(number * 3 + index) % len(LAST)], "student", [name])
                save_details(database, name, group, _schedule(cohort, group, lecturer_ids[course["id"]], tas), today=today,
                             demo_tag=None if migrated else TAG)
                meta = _row(database, "group_metadata", "name", name)
                if migrated:
                    _registry(database, "migration_config", name, {"before": old_group, "after": group,
                                                                  "metadata_before": old_meta, "metadata_after": meta})
                else:
                    _registry(database, "group", name, {"config": group, "metadata": meta, "course_id": course["id"],
                                                        "illustrative_schedule": True, "reference_date": today.isoformat()})
                for lesson_index, subject in enumerate(group["subjects"][:3]):
                    ident = f"g40-{course['id']}-{cohort['key']}-{lesson_index}"
                    lesson_day = max(cohort["start"], min(today, cohort["end"], cohort["start"] + timedelta(days=7 * (lesson_index + 1))))
                    database.conn.execute("INSERT INTO demo_lessons(id,group_name,subject,title,description,lesson_date,thumbnail,lecturer_id,duration_sec,demo_tag) "
                                          "VALUES(?,?,?,?,?,?,?,?,?,?)", (ident, name, subject["name"], subject["name"] + "․ ցուցադրական դաս",
                                            "Ցուցադրական գրադարանի քարտ է՝ առանց տեսանյութի։ Տվյալները իրական դասի գրանցում չեն։",
                                            lesson_day.isoformat(), "/brand/reel-email.png", lecturer_ids[course["id"]], 3600 + lesson_index * 300, TAG))
                    _registry(database, "lesson", ident, {})
            for row in database.conn.execute("SELECT id FROM audit_events WHERE id>?", (audit_start,)).fetchall():
                database.conn.execute("UPDATE audit_events SET demo=1 WHERE id=?", (row["id"],))
                _registry(database, "audit", row["id"], {})
            for row in database.conn.execute("SELECT * FROM account_settings").fetchall():
                if before_settings.get(row["key"]) != row["value"]:
                    _registry(database, "setting", row["key"], {"before": before_settings.get(row["key"]), "after": row["value"]})
            payload = {"demo_tag": TAG, "demo": True, "password": password,
                       "accounts": [{"login": "demo-student", "role": "student"}, {"login": "demo-ta", "role": "ta"},
                                    {"login": "demo-lecturer", "role": "lecturer"}], "note": "Ցուցադրական հաշիվներ․ մի օգտագործեք իրական տվյալների համար։"}
            credential_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
            _registry(database, "file", str(CREDENTIALS), {"sha256": _sha(credential_bytes)})
            _registry(database, "run", TAG, {"version":2,"reference_date": today.isoformat(), "backup_before": backup_before,
                                             "backup_after_sha": _sha(original_config), "replacing": replacing})
            if (root / "config.yaml").read_bytes() != original_config:
                raise DemoConflict("Configuration changed during setup; nothing was replaced")
            config_written = True
            _save_config(root, config)
            _atomic(credentials, credential_bytes, exclusive=True)
            credentials_written = True
            database.conn.commit()
            return {"status": "applied", "demo_tag": TAG, "groups": len(cohorts),
                    "users": database.conn.execute("SELECT count(*) FROM users WHERE demo_tag=?", (TAG,)).fetchone()[0],
                    "lessons": database.conn.execute("SELECT count(*) FROM demo_lessons WHERE demo_tag=?", (TAG,)).fetchone()[0],
                    "warnings": [] if any(0 <= (item["end"] - today).days <= 14 for item in cohorts) else
                    ["Fixed illustrative cohort dates have no group ending within 14 days of the reference date"]}
        except BaseException:
            database.conn.rollback()
            if credentials_written:
                credentials.unlink(missing_ok=True)
            if config_written:
                _atomic(root / "config.yaml", original_config, original_mode)
                _restore_backup(root / "config.yaml.bak", backup_before)
            raise


def remove(root=PROJECT):
    """Remove registered demos only; changed records are retained and reported."""
    from reel.config import load_config
    root = _root(root)
    config = load_config(str(root / "config.yaml"))
    original = (root / "config.yaml").read_bytes()
    backup_current = _backup(root / "config.yaml.bak")
    written = False
    deleted_credentials = None
    with _database(root) as database:
        database.conn.execute("BEGIN IMMEDIATE")
        try:
            runs = _records(database, "run")
            if not runs:
                database.conn.rollback()
                return {"status": "not_applied", "demo_tag": TAG, "conflicts": []}
            run = runs[0][2]
            conflicts = []
            registered_users = {int(key): payload for _, key, payload in _records(database, "user")}
            adopted_users = {row["id"] for row in database.conn.execute("SELECT * FROM users WHERE demo_tag=?", (TAG,))
                             if row["id"] not in registered_users or
                             _user_fingerprint(row) != registered_users[row["id"]].get("fingerprint")}
            adopted_groups = {row[0] for row in database.conn.execute('SELECT name FROM group_course_links')}
            for ident in adopted_users:
                row = _row(database, "users", "id", ident)
                adopted_groups.update(json.loads(row["groups_json"]))
            for kind, key, payload in _records(database, "lesson"):
                database.conn.execute("DELETE FROM demo_lessons WHERE id=? AND demo_tag=?", (key, TAG))
                _forget(database, kind, key)
            migration = _records(database, "migration_config")
            migration_ok = False
            if migration:
                _, key, payload = migration[0]
                current_meta = _row(database, "group_metadata", "name", key)
                known_members = {int(value) for _, value, _ in _records(database, "membership")}
                known_jobs = {int(value) for _, value, _ in _records(database, "job_group")}
                extra_members = key in adopted_groups or any(row["id"] not in known_members for row in database.conn.execute(
                    "SELECT id FROM users WHERE coalesce(demo_tag,'')!=? AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (TAG,key)))
                jobs = database.conn.execute("SELECT id,status FROM jobs WHERE grp=?", (key,)).fetchall()
                extra_or_busy_jobs = any(row["id"] not in known_jobs or row["status"] in ("queued", "running") for row in jobs)
                migration_ok = (not extra_members and not extra_or_busy_jobs and OLD_GROUP not in config["groups"]
                                and config["groups"].get(key) == payload["after"]
                                and _logical_meta(current_meta) == _logical_meta(payload["metadata_after"]))
                if migration_ok:
                    config["groups"].pop(key)
                    config["groups"][OLD_GROUP] = payload["before"]
                    database.conn.execute("DELETE FROM group_metadata WHERE name=?", (key,))
                    _restore_meta(database, OLD_GROUP, payload["metadata_before"])
                    _forget(database, "migration_config", key)
                else:
                    conflicts.append("AI 2.6 was changed; its real configuration and memberships were retained")
            for kind, key, payload in _records(database, "membership"):
                if not migration_ok:
                    continue
                row = _row(database, "users", "id", int(key))
                if row:
                    current = json.loads(row["groups_json"])
                    if REAL_GROUP in current:
                        changed = [value for value in current if value != REAL_GROUP] if REAL_GROUP not in payload["before"] else current[:]
                        if OLD_GROUP not in changed:
                            changed.insert(min(payload["before"].index(OLD_GROUP), len(changed)), OLD_GROUP)
                        database.conn.execute("UPDATE users SET groups_json=?,session_version=session_version+1 WHERE id=?", (_json(changed), int(key)))
                    elif OLD_GROUP not in current:
                        conflicts.append("A migrated membership was independently changed and retained")
                _forget(database, kind, key)
            for kind,key,payload in _records(database,'job_subject'):
                row=_row(database,'jobs','id',int(key))
                if row and row['subject']==payload['after']:
                    database.conn.execute('UPDATE jobs SET subject=? WHERE id=?',(payload['before'],int(key)))
                    _forget(database,kind,key)
                elif row:conflicts.append('A migrated job subject was independently changed and retained')
                else:_forget(database,kind,key)
            for kind, key, payload in _records(database, "job_group"):
                if not migration_ok:
                    continue
                row = _row(database, "jobs", "id", int(key))
                if row and row["grp"] == payload["after"]:
                    database.conn.execute("UPDATE jobs SET grp=? WHERE id=?", (payload["before"], int(key)))
                elif row:
                    conflicts.append("A migrated job group was independently changed and retained")
                _forget(database, kind, key)
            for kind, key, payload in _records(database, "group"):
                real_member = key in adopted_groups or database.conn.execute("SELECT 1 FROM users WHERE coalesce(demo_tag,'')!=? AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (TAG,key)).fetchone()
                meta = _row(database, "group_metadata", "name", key)
                real_job = database.conn.execute("SELECT 1 FROM jobs WHERE grp=?", (key,)).fetchone()
                if real_member or real_job or (key in config["groups"] and config["groups"][key] != payload["config"]) or (meta and _logical_meta(meta) != _logical_meta(payload["metadata"])):
                    conflicts.append("Demo group changed or has a real member; retained: " + key)
                    continue
                config["groups"].pop(key, None)
                database.conn.execute("DELETE FROM group_metadata WHERE name=? AND demo_tag=?", (key, TAG))
                database.conn.execute("DELETE FROM group_notifications WHERE name=?", (key,))
                _forget(database, kind, key)
            referenced = set()
            for row in database.conn.execute("SELECT subjects_json,ta_ids_json FROM group_metadata"):
                referenced.update(item.get("lecturer_id") for item in json.loads(row["subjects_json"]))
                referenced.update(json.loads(row["ta_ids_json"]))
            for kind, key, payload in _records(database, "user"):
                ident = int(key)
                row = _row(database, "users", "id", ident)
                if row is None:
                    _forget(database, kind, key)
                    continue
                if row["demo_tag"] != TAG or _user_fingerprint(row) != payload.get("fingerprint") or ident in referenced:
                    conflicts.append("Demo account changed or is still assigned; retained: " + payload["login"])
                    continue
                database.conn.execute("SAVEPOINT g40_demo_user")
                try:
                    database.conn.execute("DELETE FROM account_tokens WHERE user_id=?", (ident,))
                    database.conn.execute("DELETE FROM users WHERE id=? AND demo_tag=?", (ident, TAG))
                except sqlite3.IntegrityError:
                    database.conn.execute("ROLLBACK TO SAVEPOINT g40_demo_user")
                    database.conn.execute("RELEASE SAVEPOINT g40_demo_user")
                    conflicts.append("Demo account is referenced by retained data: " + payload["login"])
                    continue
                database.conn.execute("RELEASE SAVEPOINT g40_demo_user")
                _forget(database, kind, key)
            for kind, key, payload in _records(database, "audit"):
                database.conn.execute("DELETE FROM audit_events WHERE id=? AND demo=1", (int(key),))
                _forget(database, kind, key)
            for kind, key, payload in _records(database, "setting"):
                row = database.conn.execute("SELECT value FROM account_settings WHERE key=?", (key,)).fetchone()
                if row and row[0] == payload["after"]:
                    if payload["before"] is None:
                        database.conn.execute("DELETE FROM account_settings WHERE key=?", (key,))
                    else:
                        database.conn.execute("UPDATE account_settings SET value=? WHERE key=?", (payload["before"], key))
                else:
                    conflicts.append("A setting changed independently and was retained")
                _forget(database, kind, key)
            if (root / "config.yaml").read_bytes() != original:
                raise DemoConflict("Configuration changed during removal; nothing was replaced")
            written = True
            _save_config(root, config)
            expected_backup = run["backup_after_sha"]
            if backup_current and _sha(base64.b64decode(backup_current["data"])) == expected_backup:
                _restore_backup(root / "config.yaml.bak", run["backup_before"])
            else:
                _restore_backup(root / "config.yaml.bak", backup_current)
            for kind, key, payload in _records(database, "file"):
                if key != str(CREDENTIALS):
                    conflicts.append("Unrecognized demo file registration retained")
                    continue
                path = root / CREDENTIALS
                if path.is_file() and not path.is_symlink() and _sha(path.read_bytes()) == payload["sha256"]:
                    deleted_credentials = path.read_bytes()
                    path.unlink()
                    _forget(database, kind, key)
                elif not path.exists():
                    _forget(database, kind, key)
                else:
                    conflicts.append("Changed demo credentials file retained")
            remaining = [record for record in _records(database) if record[0] != "run"]
            if not remaining:
                _forget(database, "run", TAG)
            database.conn.commit()
            return {"status": "removed_with_conflicts" if conflicts else "removed", "demo_tag": TAG, "conflicts": conflicts}
        except BaseException:
            database.conn.rollback()
            if written:
                _atomic(root / "config.yaml", original, 0o644)
                _restore_backup(root / "config.yaml.bak", backup_current)
            if deleted_credentials is not None:
                _atomic(root / CREDENTIALS, deleted_credentials, exclusive=True)
            raise


def read_demo_lessons(database, groups=None):
    """Compatibility import; the application-facing reader has no seeding dependencies."""
    from reel.demo import read_demo_lessons as read
    return read(database, groups)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--remove", action="store_true")
    parser.add_argument("--root", type=Path, default=PROJECT)
    args = parser.parse_args(argv)
    try:
        report = apply(args.root) if args.apply else remove(args.root)
    except (DemoConflict, OSError, ValueError, sqlite3.Error) as error:
        print("Demo operation refused: " + (str(error) if isinstance(error, DemoConflict) else type(error).__name__), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
