"""One request boundary for personal-session validity, CSRF and group authorization."""

from reel.i18n import t as _t
import re
from contextlib import ExitStack
from reel.i18n import language_context, current_language
from reel.language import user_language
from starlette.middleware.sessions import SessionMiddleware
from starlette.datastructures import MutableHeaders
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from reel.web import accounts as a


class SecureSessionMiddleware(SessionMiddleware):
    async def __call__(self,scope,receive,send):
        async def secure_send(message):
            if message['type']=='http.response.start' and scope.get('scheme')=='https':
                headers=MutableHeaders(scope=message)
                values=headers.getlist('set-cookie')
                if values:
                    del headers['set-cookie']
                    for value in values:headers.append('set-cookie',value if '; secure' in value.lower() else value+'; Secure')
            await send(message)
        await super().__call__(scope,receive,secure_send)


def install(app,env):
    @app.middleware('http')
    async def account_boundary(request,call_next):
        token=None
        languages=ExitStack()
        try:
            path=request.url.path
            if path.startswith('/api/') and request.method in ('POST','PUT','DELETE','PATCH') and request.headers.get('x-requested-with')!='reel':
                raise HTTPException(400,_t('access.invalid_request'))
            principal=None;session=request.session
            if session.get('user_id') is not None:
                d=env['db']()
                try:
                    row=d.conn.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
                    if row and row['active'] and session.get('session_version')==row['session_version']:
                        languages.enter_context(language_context(user_language(d.conn,row['id'],current_language())))
                finally:d.conn.close()
                if not row or not row['active'] or session.get('session_version')!=row['session_version']:
                    session.clear()
                    if path.startswith('/api/'):raise HTTPException(401,_t('access.your_session_has_expired_sign_in_again'))
                else:
                    principal=a.public_user(row);session['role']=row['role']
            elif session.get('bootstrap'):
                d=env['db']()
                try:exists=d.conn.execute('SELECT count(*) FROM users').fetchone()[0]
                finally:d.conn.close()
                if exists:session.clear();raise HTTPException(401,_t('access.sign_in_again'))
                if path.startswith('/api/') and path not in ('/api/me','/api/logout','/api/accounts/bootstrap'):
                    raise HTTPException(403,_t('access.create_a_personal_administrator_account_first'))
            elif session.get('role'):
                d=env['db']()
                try:
                    enabled=a.legacy_enabled(d)
                    user_count=d.conn.execute('SELECT count(*) FROM users').fetchone()[0]
                finally:d.conn.close()
                if session.get('role')=='admin':
                    if user_count:
                        session.clear();raise HTTPException(401,_t('access.sign_in_with_a_personal_administrator_account'))
                    session.clear();session.update(role='bootstrap',bootstrap=True)
                    if path.startswith('/api/') and path not in ('/api/me','/api/logout','/api/accounts/bootstrap'):
                        raise HTTPException(403,_t('access.create_an_administrator_account_first'))
                if not enabled:session.clear();raise HTTPException(401,_t('access.sign_in_with_a_personal_account'))
                principal={'role':session['role'],'legacy':True,'groups':[]}
            request.state.principal=principal;token=a.PRINCIPAL.set(principal)
            if principal and not principal.get('legacy') and principal['role']!='admin':
                match=re.match(r'^/api/jobs/([^/]+)(?:/|$)',path)
                if match:
                    try:jid=int(match[1])
                    except ValueError:jid=None
                    if jid is not None:
                        d=env['db']()
                        try:
                            try:job=d.job(jid)
                            except KeyError:raise HTTPException(404,_t('access.not_found')) from None
                            a.require_group(job['grp'])
                        finally:d.conn.close()
                if path=='/api/uploads/next-number':a.require_group(request.query_params.get('group'))
                match=re.match(r'^/api/uploads/([a-f0-9]{20})(?:/|$)',path)
                if match:
                    uid=match[1];meta=env['_read_json'](env['_uploads']()/uid/'meta.json')
                    if meta:a.require_group(meta.get('group'))
                    else:
                        receipt=env['_upload_receipt'](uid)
                        if receipt:
                            jid=receipt.get('job_id') or receipt.get('duplicate_of');d=env['db']()
                            try:
                                try:a.require_group(d.job(jid)['grp'])
                                except KeyError:raise HTTPException(404,_t('access.not_found')) from None
                            finally:d.conn.close()
            response=await call_next(request)
            if path.startswith('/api/') and 'cache-control' not in response.headers:
                response.headers['Cache-Control']='private, no-store'
            # Tokens are in fragments/JSON, never request URLs; do not allow referrer propagation.
            if path.startswith('/api/accounts'):
                response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer'
            return response
        except HTTPException as e:
            return JSONResponse({'detail':e.detail},status_code=e.status_code,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})
        finally:
            if token is not None:a.PRINCIPAL.reset(token)
            languages.close()
    # Added last so the existing cookie is available to every request guard above.
    app.add_middleware(SecureSessionMiddleware,secret_key=env['SECRETS']['session_key'],
                       session_cookie='reel_session',max_age=7*24*3600,same_site='lax')
