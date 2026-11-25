"""Admin-only audit listing and request events; no passwords or request bodies are logged."""
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from reel.audit import audit, audit_context


def make_router(env):
    router = APIRouter(prefix='/api/admin/audit', dependencies=[Depends(env['admin'])])

    @router.get('')
    def listing(actor: int | None = None, action: str = '', page: int = 1, page_size: int = 30,
                request: Request = None):
        if page < 1 or page > 1_000_000 or not 1 <= page_size <= 100:
            raise HTTPException(400, 'Ընտրեք վավեր էջ և էջի չափ։')
        conditions, args = [], []
        if actor is not None:
            if actor < 0 or actor > 9223372036854775807:
                raise HTTPException(400, 'Ընտրեք վավեր օգտատեր։')
            conditions.append('e.actor IS NULL' if actor == 0 else 'e.actor=?')
            if actor:
                args.append(actor)
        if action:
            if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,59}', action):
                raise HTTPException(400, 'Ընտրեք վավեր գործողություն։')
            conditions.append('e.action=?');args.append(action)
        bounds = []
        for key, operator in (('from', '>='), ('to', '<')):
            raw = request.query_params.get(key, '')
            if not raw:
                continue
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                    raise ValueError()
                parsed = date.fromisoformat(raw)
                bounds.append((key, parsed))
                if key == 'to':
                    parsed += timedelta(days=1)
                boundary = datetime.combine(parsed, time(), ZoneInfo('Asia/Yerevan')).astimezone(timezone.utc)
            except (ValueError, OverflowError):
                raise HTTPException(400, 'Ամսաթիվը նշեք ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով։') from None
            conditions.append('e.created_at' + operator + '?');args.append(boundary.isoformat(timespec='seconds'))
        if len(bounds) == 2 and bounds[0][1] > bounds[1][1]:
            raise HTTPException(400, 'Ավարտի ամսաթիվը չի կարող սկզբից շուտ լինել։')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        database = env['db']()
        try:
            total = database.conn.execute('SELECT count(*) FROM audit_events e' + where, args).fetchone()[0]
            rows = database.conn.execute("SELECT e.*,coalesce(u.name,'Համակարգ') actor_name FROM audit_events e "
                'LEFT JOIN users u ON u.id=e.actor' + where + ' ORDER BY e.id DESC LIMIT ? OFFSET ?',
                (*args, page_size, (page - 1) * page_size)).fetchall()
            actors = database.conn.execute("SELECT DISTINCT e.actor id,coalesce(u.name,'Համակարգ') name "
                'FROM audit_events e LEFT JOIN users u ON u.id=e.actor ORDER BY name').fetchall()
            actions = [row[0] for row in database.conn.execute('SELECT DISTINCT action FROM audit_events ORDER BY action')]
            return {'items': [dict(row) | {'details': json.loads(row['details'])} for row in rows],
                    'actors': [dict(row) for row in actors], 'actions': actions,
                    'total': total, 'page': page, 'pages': max(1, (total + page_size - 1) // page_size)}
        finally:
            database.conn.close()

    return router


def install(app, env):
    def snapshot(uid):
        if not 1 <= uid <= 9223372036854775807:
            return {}
        database = env['db']()
        try:
            row = database.conn.execute('SELECT role,groups_json,active FROM users WHERE id=?', (uid,)).fetchone()
            return {'role': row['role'], 'groups': json.loads(row['groups_json']), 'active': bool(row['active'])} if row else {}
        finally:
            database.conn.close()

    @app.middleware('http')
    async def record(request: Request, call_next):
        path = request.url.path
        principal = getattr(request.state, 'principal', None)
        actor = principal.get('id') if principal else None
        match = re.fullmatch(r'/api/accounts/users/(\d+)(?:/(remove|restore|delete|reset))?', path)
        before = snapshot(int(match[1])) if actor and match and not match[2] and request.method == 'POST' else None
        with audit_context(None, actor):
            response = await call_next(request)
        if not actor or not 200 <= response.status_code < 300 or request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
            return response
        events = []
        if match:
            uid, operation = int(match[1]), match[2]
            if operation in ('remove', 'restore', 'delete'):
                events.append(('user.' + operation, 'user:' + str(uid), {}))
            elif not operation and before is not None:
                after = snapshot(uid)
                for field in ('role', 'groups', 'active'):
                    if before.get(field) != after.get(field):
                        events.append(('user.' + field, 'user:' + str(uid),
                                       {'field': field, 'before': before.get(field), 'after': after.get(field)}))
        elif re.fullmatch(r'/api/accounts/invitations(?:/\d+/(?:resend|revoke))?', path):
            parts = path.strip('/').split('/')
            operation = parts[-1] if parts[-1] in ('resend', 'revoke') else 'create'
            target = 'invite:' + parts[-2] if operation != 'create' else 'invitation'
            events.append(('invite.' + operation, target, {}))
        elif path.startswith('/api/recordings'):
            operation=('start' if path=='/api/recordings' else 'confirm' if path.endswith('/confirm') else 'close' if path.endswith('/close') else 'part' if path.endswith('/parts') else 'part_delete' if '/parts/' in path and request.method=='DELETE' else 'cancel' if request.method=='DELETE' else None)
            if operation:events.append(('recording.'+operation,'recording',{}))
        elif path.startswith('/api/admin/groups/'):
            events.append(('group.delete' if request.method == 'DELETE' else 'group.save',
                           'group:' + path.removeprefix('/api/admin/groups/'), {}))
        if events:
            database = env['db']()
            try:
                with audit_context(database):
                    for action, target, details in events:
                        audit(actor, action, target, details)
            finally:
                database.conn.close()
        return response
