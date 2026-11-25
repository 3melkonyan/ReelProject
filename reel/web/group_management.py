"""Group calendars and display-only teams, kept out of worker configuration."""
from __future__ import annotations

from reel.i18n import t as _t

from contextlib import closing
from datetime import date
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from reel.db import now
from reel.web import accounts
from reel.web.group_status import calendar_day, completed_groups, legacy_completed, store_legacy_completed

END_SOON_DAYS = 14


def _metadata(database, name):
    row = database.conn.execute('SELECT * FROM group_metadata WHERE name=?', (name,)).fetchone()
    return dict(row) if row else {'name':name, 'course_start':None, 'course_end':None,
                                 'subjects_json':'[]', 'ta_ids_json':'[]', 'status_override':None, 'demo_tag':None}


def _person(row):
    return {'id':row['id'], 'name':row['name'],
            'avatar':f"/api/accounts/avatars/{row['id']}?v={row['avatar']}" if row['avatar'] else None}


def eligible_people(database, name, role):
    rows = database.conn.execute("SELECT * FROM users WHERE role=? AND active=1 AND removed_at IS NULL "
                                 "AND deleted_at IS NULL AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?) ORDER BY casefold(name),id",
                                 (role, name)).fetchall()
    return [_person(row) for row in rows]


def group_snapshot(database, name, group, today=None):
    meta = _metadata(database, name)
    lecturers = {person['id']:person for person in eligible_people(database, name, 'lecturer')}
    tas = {person['id']:person for person in eligible_people(database, name, 'student')}
    periods = {item['name'].casefold():item for item in json.loads(meta['subjects_json'])}
    subjects, team = [], {'lecturers':[], 'tas':[]}
    for subject in group['subjects']:
        stored = periods.get(subject['name'].casefold(), {})
        lecturer = lecturers.get(stored.get('lecturer_id'))
        subjects.append({'name':subject['name'], 'lecturer_id':lecturer['id'] if lecturer else None,
                         'start_date':stored.get('start_date'), 'end_date':stored.get('end_date'), 'lecturer':lecturer})
        if lecturer:
            team['lecturers'].append({'subject':subject['name'], **lecturer})
    team['tas'] = [tas[ident] for ident in json.loads(meta['ta_ids_json']) if ident in tas]
    students = database.conn.execute("SELECT count(*) FROM users WHERE role='student' AND removed_at IS NULL "
                                    "AND deleted_at IS NULL AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?)", (name,)).fetchone()[0]
    lessons = database.conn.execute('SELECT count(*) FROM jobs WHERE grp=?', (name,)).fetchone()[0]
    lessons += database.conn.execute('SELECT count(*) FROM demo_lessons WHERE group_name=?', (name,)).fetchone()[0]
    completed = name in completed_groups(database, today)
    days_left = (date.fromisoformat(meta['course_end']) - calendar_day(today)).days if meta['course_end'] else None
    return {'name':name, 'course_start':meta['course_start'], 'course_end':meta['course_end'],
            'subjects':subjects, 'ta_ids':[person['id'] for person in team['tas']], 'team':team,
            'status':'completed' if completed else 'active', 'completed':completed,
            'manual_active':meta['status_override']=='active', 'days_left':days_left,
            'end_soon':not completed and days_left is not None and 0 <= days_left <= END_SOON_DAYS,
            'counts':{'students':students, 'lessons':lessons, 'classes':lessons},
            'student_count':students, 'lesson_count':lessons, 'subject_count':len(subjects)}


def _dates(start, end, label):
    def clean(value):
        if value in (None, ''):
            return None
        if not isinstance(value, str):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return value
    try:
        start, end = clean(start), clean(end)
        if (start is None) != (end is None) or (start and end < start):
            raise ValueError()
        return start, end
    except (ValueError, TypeError):
        raise HTTPException(400, label + _t('group_management.enter_start_and_end_dates_in_yyyy')) from None


def save_details(database, name, group, payload, *, actor=None, today=None, demo_tag=None):
    """Validate membership under the same write reservation as the saved assignment."""
    from reel.audit import write_event
    if not isinstance(payload, dict) or set(payload) != {'course_start','course_end','subjects','ta_ids'}:
        raise HTTPException(400, _t('group_management.enter_course_dates_subjects_and_assistants'))
    start, end = _dates(payload['course_start'], payload['course_end'], _t('group_management.course_dates'))
    if not isinstance(payload['subjects'], list) or len(payload['subjects']) != len(group['subjects']):
        raise HTTPException(400, _t('group_management.include_each_group_subject_exactly_once'))
    configured = {subject['name'].casefold():subject['name'] for subject in group['subjects']}
    seen, periods = set(), []
    for item in payload['subjects']:
        if not isinstance(item, dict) or set(item) != {'name','lecturer_id','start_date','end_date'} or not isinstance(item['name'], str):
            raise HTTPException(400, _t('group_management.enter_the_subject_name_lecturer_and_dates'))
        key = item['name'].casefold()
        if key not in configured or key in seen:
            raise HTTPException(400, _t('group_management.a_subject_is_missing_or_duplicated'))
        seen.add(key)
        begin, finish = _dates(item['start_date'], item['end_date'], _t('group_management.subject_dates'))
        if begin and (not start or begin < start or finish > end):
            raise HTTPException(400, _t('group_management.subject_dates_must_fall_within_the_course'))
        ident = item['lecturer_id']
        if ident is not None and (type(ident) is not int or ident <= 0):
            raise HTTPException(400, _t('group_management.choose_an_active_lecturer_account_assigned_to'))
        periods.append({'name':configured[key], 'lecturer_id':ident, 'start_date':begin, 'end_date':finish})
    ta_ids = payload['ta_ids']
    if not isinstance(ta_ids, list) or len(ta_ids) > 100 or any(type(ident) is not int or ident <= 0 for ident in ta_ids):
        raise HTTPException(400, _t('group_management.choose_assistants_from_the_group_s_active'))
    ta_ids = list(dict.fromkeys(ta_ids))
    owns_transaction = not database.conn.in_transaction
    if owns_transaction:
        database.conn.execute('BEGIN IMMEDIATE')
    try:
        lecturer_ids = {person['id'] for person in eligible_people(database, name, 'lecturer')}
        student_ids = {person['id'] for person in eligible_people(database, name, 'student')}
        if any(period['lecturer_id'] is not None and period['lecturer_id'] not in lecturer_ids for period in periods):
            raise HTTPException(400, _t('group_management.the_lecturer_must_be_an_active_lecturer'))
        if any(ident not in student_ids for ident in ta_ids):
            raise HTTPException(400, _t('group_management.the_assistant_must_be_an_active_student'))
        old = _metadata(database, name)
        override = old['status_override']
        if end != old['course_end'] and end and end >= calendar_day(today).isoformat():
            override = None
            done = legacy_completed(database); done.discard(name); store_legacy_completed(database, done)
        tag = old['demo_tag'] if demo_tag is None else demo_tag
        database.conn.execute('INSERT INTO group_metadata(name,course_start,course_end,subjects_json,ta_ids_json,status_override,updated_at,demo_tag) '
                              'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET course_start=excluded.course_start,course_end=excluded.course_end,'
                              'subjects_json=excluded.subjects_json,ta_ids_json=excluded.ta_ids_json,status_override=excluded.status_override,updated_at=excluded.updated_at,demo_tag=excluded.demo_tag',
                              (name,start,end,json.dumps(periods,ensure_ascii=False),json.dumps(ta_ids),override,now(),tag))
        changed_dates = (start,end,[(p['name'],p['start_date'],p['end_date']) for p in periods]) != (
            old['course_start'],old['course_end'],[(p['name'],p.get('start_date'),p.get('end_date')) for p in json.loads(old['subjects_json'])])
        if changed_dates:
            write_event(database, actor=actor, action='group.update', target='group:'+name, details={'fields':['schedule'],'before':{'course_start':old['course_start'],'course_end':old['course_end']},
                        'after':{'course_start':start,'course_end':end}}, demo=bool(tag))
        if [(p['name'],p['lecturer_id']) for p in periods] != [(p['name'],p.get('lecturer_id')) for p in json.loads(old['subjects_json'])] or ta_ids != json.loads(old['ta_ids_json']):
            write_event(database, actor=actor, action='group.team', target='group:'+name, details={'fields':['team'],
                        'before':{'ta_ids':json.loads(old['ta_ids_json']),'lecturer_ids':[p.get('lecturer_id') for p in json.loads(old['subjects_json'])]},
                        'after':{'ta_ids':ta_ids,'lecturer_ids':[p['lecturer_id'] for p in periods]}}, demo=bool(tag))
        if owns_transaction:
            database.conn.execute('COMMIT')
        return group_snapshot(database, name, group, today)
    except BaseException:
        if owns_transaction and database.conn.in_transaction:
            database.conn.execute('ROLLBACK')
        raise


def notify_expired(database, config, notifier, today=None):
    """Claim one attempt per end date; callback failure must not spam subsequent requests."""
    from reel.audit import write_event
    if notifier is None:
        return []
    if database.conn.in_transaction:
        raise RuntimeError('expiry notifications require a committed database connection')
    current = calendar_day(today).isoformat()
    completed = completed_groups(database, today)
    results = []
    for name, group in config['groups'].items():
        meta = _metadata(database, name)
        if name not in completed or not meta['course_end'] or meta['course_end'] >= current:
            continue
        demo = meta['demo_tag'] or group.get('demo') or database.conn.execute(
            "SELECT 1 FROM demo_registry WHERE kind IN ('group','config_group') AND key=? LIMIT 1", (name,)).fetchone()
        if demo:
            continue
        claimed = database.conn.execute('INSERT OR IGNORE INTO group_notifications(name,end_date,claimed_at) VALUES(?,?,?)',
                                        (name,meta['course_end'],now()))
        if claimed.rowcount != 1:
            continue
        write_event(database, actor=0, action='group.complete', target='group:'+name,
                    details={'reason':'schedule','end_date':meta['course_end']})
        sent = False
        try:
            notifier(group['slack_channel'], _t('group_management.the_course_for_group_group_ended_on', group=name, date=meta['course_end']))
            sent = True
        except Exception:
            pass
        if sent:
            database.conn.execute('UPDATE group_notifications SET sent_at=? WHERE name=? AND end_date=?', (now(),name,meta['course_end']))
        results.append({'name':name,'sent':sent})
    return results


def reconcile_metadata(database, old_name, new_name=None, subjects=None):
    """Run inside the core-config transaction; completed jobs and memberships stay untouched."""
    row = database.conn.execute('SELECT * FROM group_metadata WHERE name=?', (old_name,)).fetchone()
    done = legacy_completed(database)
    if new_name is None:
        database.conn.execute('DELETE FROM group_course_links WHERE name=?',(old_name,))
        database.conn.execute('DELETE FROM group_metadata WHERE name=?', (old_name,))
        database.conn.execute('DELETE FROM group_notifications WHERE name=?', (old_name,))
        done.discard(old_name)
    else:
        if old_name != new_name:
            database.conn.execute('UPDATE group_course_links SET name=? WHERE name=?',(new_name,old_name))
            database.conn.execute('UPDATE group_metadata SET name=? WHERE name=?', (new_name,old_name))
            database.conn.execute('UPDATE group_notifications SET name=? WHERE name=?', (new_name,old_name))
            if old_name in done:
                done.remove(old_name); done.add(new_name)
        if row and subjects is not None:
            previous = {item['name'].casefold():item for item in json.loads(row['subjects_json'])}
            periods = [{**previous[subject['name'].casefold()], 'name':subject['name']} for subject in subjects if subject['name'].casefold() in previous]
            database.conn.execute('UPDATE group_metadata SET subjects_json=? WHERE name=?', (json.dumps(periods,ensure_ascii=False),new_name))
    store_legacy_completed(database, done)


def _mask(value):
    value = str(value or '')
    return value if len(value) <= 6 else value[:3] + '•••' + value[-3:]


def _styles(root, config):
    from reel import thumbnail
    path = root / 'thumbnail_styles.json'
    if path.is_symlink():
        raise HTTPException(409, _t('group_management.invalid_thumbnail_styles_file_path'))
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(stored,dict):raise ValueError()
    except FileNotFoundError:
        stored = {}
    except (OSError,ValueError):
        raise HTTPException(409, _t('group_management.the_thumbnail_styles_file_cannot_be_read')) from None
    used = {value for value in stored.values() if type(value) is int and 0 <= value < len(thumbnail.STYLES)}
    result = {}
    for name in config['groups']:
        style = stored.get(name)
        if type(style) is not int or not 0 <= style < len(thumbnail.STYLES):
            style = next((index for index in range(len(thumbnail.STYLES)) if index not in used),len(used)%len(thumbnail.STYLES))
            used.add(style)
        result[name] = style
    return result


def make_router(env):
    router = APIRouter()
    def known():
        from reel.config import load_config
        return load_config(str(env['ROOT']/'config.yaml'))
    def group(config,name):
        accounts.require_group(name)
        if name not in config['groups']:
            raise HTTPException(404,_t('academy_api.group_not_found'))
        return config['groups'][name]
    @router.get('/api/group-cards')
    def cards(status:str='all',_=Depends(env['role'])):
        if status not in ('all','active','completed'):
            raise HTTPException(400,_t('group_management.choose_a_valid_group_status'))
        config = known();database = env['db']()
        with closing(database.conn):
            # Only an explicitly supplied callback can send a notification.
            scoped = {**config,'groups':{name:item for name,item in config['groups'].items() if accounts.allowed(name)}}
            notify_expired(database,scoped,env.get('notify_group_expiry'))
            styles = _styles(env['ROOT'],config)
            snapshots = []
            for name,item in scoped['groups'].items():
                snapshot = group_snapshot(database,name,item)
                if status!='all' and snapshot['status']!=status:
                    continue
                for subject,original in zip(snapshot['subjects'],item['subjects'], strict=False):
                    subject['playlist'] = _mask(original.get('playlist'))
                snapshots.append({**snapshot,'slack':_mask(item.get('slack_channel')),
                                  'classroom':_mask(item.get('classroom_course_id')),
                                  'thumbnail_style':styles[name],
                                  'thumbnail_url':'/api/group-cards/'+quote(name,safe='')+'/thumbnail.png'})
        return {'groups':snapshots}
    @router.get('/api/group-cards/{name}/thumbnail.png')
    def thumbnail(name:str,_=Depends(env['role'])):
        from reel.web.thumbnail_styles import _preview
        config=known();group(config,name)
        return Response(_preview(_styles(env['ROOT'],config)[name]),media_type='image/png',headers={'Cache-Control':'private, no-store'})
    @router.get('/api/groups/{name}/team')
    def team(name:str,_=Depends(env['authenticated'])):
        config=known();item=group(config,name);database=env['db']()
        with closing(database.conn):snapshot=group_snapshot(database,name,item)
        return {'name':name,'team':snapshot['team']}
    @router.get('/api/admin/group-details/{name}',dependencies=[Depends(env['admin'])])
    def details(name:str):
        config=known();item=group(config,name);database=env['db']()
        with closing(database.conn):
            snapshot=group_snapshot(database,name,item)
            snapshot['options']={'lecturers':eligible_people(database,name,'lecturer'),'tas':eligible_people(database,name,'student')}
        return snapshot
    @router.put('/api/admin/group-details/{name}',dependencies=[Depends(env['admin'])])
    async def put_details(name:str,request:Request):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>32768:raise HTTPException(413,_t('account_api.request_too_large'))
        try:payload=json.loads(raw)
        except (ValueError,UnicodeError):raise HTTPException(400,_t('access.invalid_request')) from None
        def save():
            from reel.web.admin import _CONFIG_LOCK
            with _CONFIG_LOCK:
                config=known();item=group(config,name);database=env['db']()
                with closing(database.conn):
                    principal=getattr(request.state,'principal',None) or {}
                    result=save_details(database,name,item,payload,actor=principal.get('id'))
                    result['options']={'lecturers':eligible_people(database,name,'lecturer'),'tas':eligible_people(database,name,'student')}
                    return result
        return await run_in_threadpool(save)
    return router
