"""Account/profile/admin routes on Reel's existing SessionMiddleware cookie."""
from __future__ import annotations

from reel.i18n import t as _t
from contextlib import closing
import io,json,re,secrets,time,warnings
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter,Depends,HTTPException,Request,BackgroundTasks
from fastapi.responses import FileResponse
from reel.web import accounts as a


def make_router(env):
    router=APIRouter(prefix='/api/accounts')
    def personal(request:Request):
        p=getattr(request.state,'principal',None)
        if not p or not p.get('id'):raise HTTPException(401,_t('access.sign_in_with_a_personal_account'))
        return p
    def administrator(p=Depends(personal)):
        if p['role']!='admin':raise HTTPException(403,_t('account_api.administrators_only'))
        return p
    def known():
        from reel.config import load_config
        return load_config(str(env['ROOT']/'config.yaml'))['groups']
    async def body(request):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>16384:raise HTTPException(413,_t('account_api.request_too_large'))
        try:
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError()
            return value
        except (ValueError,UnicodeError):raise HTTPException(400,_t('access.invalid_request')) from None
    @router.post('/bootstrap')
    async def bootstrap(request:Request):
        if not request.session.get('bootstrap'):raise HTTPException(403,_t('account_api.sign_in_with_the_current_administrator_password'))
        data=await body(request);payload=a.fields(data.get('name'),data.get('email'),'admin',[],known())
        from reel.web import privacy
        consent=privacy.validate(data,payload)
        d=env['db']()
        try:
            privacy.ensure(d.conn)
            d.conn.execute('BEGIN IMMEDIATE')
            if d.conn.execute('SELECT count(*) FROM users').fetchone()[0]:raise HTTPException(409,_t('account_api.an_administrator_account_has_already_been_created'))
            uid=a.create_user(d,data.get('login'),data.get('password'),payload)
            privacy.record(d.conn,uid,consent)
            d.conn.execute('COMMIT')
            row=d.conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if row['role']=='admin':return await env['_admin_login'](request,row,d,env)
            row=a.session_user(request,row,d)
            return {'role':'admin','user':a.public_user(row)}
        except sqlite3.IntegrityError:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise HTTPException(409,_t('account_api.these_account_details_are_already_in_use')) from None
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    @router.post('/invitation')
    async def invitation(request:Request):
        data=await body(request)
        with closing(env['db']().conn) as conn:
            from types import SimpleNamespace
            row=a.token_row(SimpleNamespace(conn=conn),data.get('token'),'invite')
            from reel.web.privacy import junior
            payload=json.loads(row['payload'])
            return {**payload,'guardian_required':junior(payload,conn)}
    @router.post('/register')
    async def register(request:Request):
        data=await body(request);d=env['db']()
        try:
            from reel.web import privacy
            invitation=a.token_row(d,data.get('token'),'invite')
            consent=privacy.validate(data,json.loads(invitation['payload']),d.conn)
            uid=a.redeem(d,data.get('token'),'invite',login=data.get('login'),password=data.get('password'),known=known(),consent=consent)
            row=d.conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if row['role']=='admin':return await env['_admin_login'](request,row,d,env)
            row=a.session_user(request,row,d)
            return {'role':row['role'],'user':a.public_user(row)}
        finally:d.conn.close()
    @router.post('/forgot')
    async def forgot(request:Request,tasks:BackgroundTasks):
        data=await body(request);value=str(data.get('login') or '')[:254];d=env['db']()
        try:
            # Use the same response and asynchronous email path for known/unknown identities.
            a.login_limit(d,'reset:'+value,request.client.host if request.client else '?')
            row=a.find_user(d,value)
            if row and row['active']:
                _,raw=a.issue_token(d,'reset',user_id=row['id'])
                tasks.add_task(a.send_reset,env['ROOT'],row['email'],a.link(env['ROOT'],'reset',raw))
            a.login_limit(d,'reset:'+value,request.client.host if request.client else '?',failed=True)
        except HTTPException:pass
        finally:d.conn.close()
        return {'message':_t('account_api.if_an_active_account_matches_these_details')}
    @router.post('/reset')
    async def reset(request:Request):
        data=await body(request);d=env['db']()
        try:a.redeem(d,data.get('token'),'reset',password=data.get('password'))
        finally:d.conn.close()
        request.session.clear();return {'ok':True}
    @router.get('/profile')
    def profile(p=Depends(personal)):
        d=env['db']()
        try:return a.public_user(d.conn.execute('SELECT * FROM users WHERE id=?',(p['id'],)).fetchone())
        finally:d.conn.close()
    @router.post('/profile')
    async def name(request:Request,p=Depends(personal)):
        data=await body(request);name=data.get('name')
        if not isinstance(name,str) or not name.strip() or len(name)>120:raise HTTPException(400,_t('account_api.enter_a_name_of_up_to_120'))
        with closing(env['db']().conn) as conn:conn.execute('UPDATE users SET name=? WHERE id=?',(name.strip(),p['id']))
        return {'ok':True}
    @router.post('/password')
    async def password(request:Request,p=Depends(personal)):
        data=await body(request);hashed=a.hash_password(data.get('password'));d=env['db']()
        try:
            d.conn.execute('BEGIN IMMEDIATE');row=d.conn.execute('SELECT * FROM users WHERE id=?',(p['id'],)).fetchone()
            if not a.verify_password(data.get('current_password'),row['password_hash']):raise HTTPException(400,_t('account_api.the_current_password_is_incorrect'))
            d.conn.execute('UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=?',(hashed,p['id']))
            d.conn.execute("UPDATE account_tokens SET revoked_at=? WHERE user_id=? AND kind='reset' AND used_at IS NULL",(time.time(),p['id']))
            d.conn.execute('COMMIT');request.session.clear();return {'ok':True}
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    def avatar_path(name):
        folder=env['ROOT']/'avatars'
        if folder.is_symlink() or not re.fullmatch(r'\d+-[0-9a-f]{32}\.jpg',name or ''):raise HTTPException(404,_t('access.not_found'))
        path=folder/name
        if path.is_symlink():raise HTTPException(404,_t('access.not_found'))
        return path
    @router.post('/avatar')
    async def avatar(request:Request,p=Depends(personal)):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>5*1024*1024:raise HTTPException(413,_t('account_api.the_maximum_image_size_is_5_mb'))
        try:
            # Header preflight rejects decompression bombs BEFORE cv2 allocates pixels.
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as header:
                    if header.format not in ('JPEG','PNG','WEBP') or max(header.size)>8000:raise ValueError()
            import cv2,numpy as np
            image=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None or max(image.shape[:2])>8000 or min(image.shape[:2])<=0:raise ValueError()
            h,w=image.shape[:2];side=min(h,w);image=image[(h-side)//2:(h+side)//2,(w-side)//2:(w+side)//2]
            image=cv2.resize(image,(256,256),interpolation=cv2.INTER_AREA)
            ok,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,90])
            if not ok:raise ValueError()
        except Exception:raise HTTPException(400,_t('account_api.choose_a_valid_jpeg_png_or_webp')) from None
        filename=f"{p['id']}-{secrets.token_hex(16)}.jpg";path=avatar_path(filename);path.parent.mkdir(exist_ok=True)
        path.write_bytes(encoded.tobytes())
        with closing(env['db']().conn) as conn:conn.execute('UPDATE users SET avatar=? WHERE id=?',(filename,p['id']))
        return {'avatar':f"/api/accounts/avatars/{p['id']}?v={filename}"}
    @router.get('/avatars/{user_id}')
    def picture(user_id:int,_=Depends(env['authenticated'])):
        with closing(env['db']().conn) as conn:row=conn.execute('SELECT avatar FROM users WHERE id=? AND active=1',(user_id,)).fetchone()
        if not row or not row[0]:raise HTTPException(404,_t('access.not_found'))
        path=avatar_path(row[0])
        if not path.is_file():raise HTTPException(404,_t('access.not_found'))
        return FileResponse(path,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})
    def completed(d):
        from reel.web.group_status import completed_groups
        return completed_groups(d)
    def assignable(d, selected, previous=()):
        # Existing completed memberships remain editable; new assignments do not reopen groups.
        done=completed(d)
        if any(group in done and group not in previous for group in selected):
            raise HTTPException(400,_t('account_api.you_cannot_add_new_users_to_a'))
    def public_available():
        base=a.public_base(env['ROOT'])
        return bool(base and urlsplit(base).hostname not in ('localhost','127.0.0.1','::1'))
    def actor_current(d,p):
        actor=d.conn.execute("SELECT * FROM users WHERE id=?",(p['id'],)).fetchone()
        if not actor or not actor['active'] or actor['role']!='admin' or actor['removed_at'] or actor['deleted_at']:
            raise HTTPException(403,_t('account_api.an_active_administrator_account_is_required'))
    def mail_result(payload, ident, raw, warnings):
        from reel.web import account_mail
        url=a.link(env['ROOT'],'invite',raw);available=public_available()
        sent=available and account_mail.send_invitation(env['ROOT'],payload['email'],url,payload['name'])
        return {'id':ident,'link':url,'mail_sent':bool(sent),'warnings':warnings,
                'public_available':available,'public_access':available}
    @router.get('/directory')
    def directory(request:Request,_=Depends(administrator)):
        d=env['db']()
        try:return a.directory(d,known(),request.query_params,completed(d))
        finally:d.conn.close()
    @router.get('/users')
    def users(_=Depends(administrator)):
        with closing(env['db']().conn) as conn:
            rows=conn.execute('SELECT * FROM users WHERE removed_at IS NULL AND deleted_at IS NULL ORDER BY id').fetchall()
        return {'users':[a.public_user(r) for r in rows],'groups':list(known())}
    @router.get('/users/{uid}')
    def user_detail(uid:int,_=Depends(administrator)):
        with closing(env['db']().conn) as conn:row=conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not row:raise HTTPException(404,_t('access.not_found'))
        return a.public_user(row)
    @router.post('/users/{uid}')
    async def edit_user(uid:int,request:Request,p=Depends(administrator)):
        data=await body(request);d=env['db']()
        try:
            d.conn.execute('BEGIN IMMEDIATE');actor_current(d,p)
            row=d.conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not row or row['deleted_at']:raise HTTPException(404,_t('access.not_found'))
            if row['removed_at']:raise HTTPException(409,_t('account_api.restore_the_removed_account_first'))
            previous=json.loads(row['groups_json'])
            fields=a.fields(row['name'],row['email'],data.get('role',row['role']),data.get('groups',previous),known())
            assignable(d,fields['groups'],previous)
            active=data.get('active',bool(row['active']))
            if type(active) is not bool:raise HTTPException(400,_t('account_api.choose_whether_the_account_is_active'))
            loses_admin=row['role']=='admin' and fields['role']!='admin'
            a.protect_admin(d,row,p['id'],disabling=not active or loses_admin)
            changed=fields['role']!=row['role'] or bool(row['active'])!=active or set(fields['groups'])!=set(previous)
            d.conn.execute('UPDATE users SET role=?,groups_json=?,active=?,session_version=session_version+? WHERE id=?',
                           (fields['role'],json.dumps(fields['groups']),int(active),int(changed),uid))
            if changed:a.revoke_user_tokens(d,row)
            d.conn.execute('COMMIT');return {'ok':True}
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    def lifecycle(uid,action,p):
        d=env['db']();avatar_files=[]
        try:
            d.conn.execute('BEGIN IMMEDIATE');actor_current(d,p)
            row=d.conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not row or row['deleted_at']:raise HTTPException(404,_t('access.not_found'))
            if action=='restore':
                if not row['removed_at']:raise HTTPException(409,_t('account_api.the_account_has_not_been_removed'))
                d.conn.execute('UPDATE users SET removed_at=NULL,active=1,session_version=session_version+1 WHERE id=?',(uid,))
                a.revoke_user_tokens(d,row)
            else:
                a.protect_admin(d,row,p['id'])
                if action=='delete':
                    if not row['removed_at']:raise HTTPException(409,_t('account_api.remove_the_account_before_permanently_deleting_it'))
                    a.revoke_user_tokens(d,row,anonymize=True)
                    anonymous=f'deleted-{uid}-{secrets.token_hex(8)}'
                    d.conn.execute('UPDATE users SET login=?,email=?,name=?,password_hash=?,groups_json=?,avatar=NULL,active=0,last_login_at=NULL,deleted_at=?,session_version=session_version+1 WHERE id=?',
                                   (anonymous,anonymous+'@deleted.invalid',_t(a.DELETED_USER_LABEL_KEY),'!deleted','[]',a.utc(),uid))
                    folder=env['ROOT']/'avatars'
                    if not folder.is_symlink() and folder.is_dir():
                        avatar_files=[path for path in folder.glob(f'{uid}-*.jpg')
                                      if re.fullmatch(r'\d+-[0-9a-f]{32}\.jpg',path.name) and not path.is_symlink() and path.is_file()]
                else:
                    a.revoke_user_tokens(d,row)
                    d.conn.execute('UPDATE users SET removed_at=coalesce(removed_at,?),active=0,session_version=session_version+1 WHERE id=?',(a.utc(),uid))
            d.conn.execute('COMMIT')
            for path in avatar_files:
                try:path.unlink(missing_ok=True)
                except OSError:pass
            return {'ok':True}
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    @router.post('/users/{uid}/remove')
    def remove(uid:int,p=Depends(administrator)):return lifecycle(uid,'remove',p)
    @router.post('/users/{uid}/restore')
    def restore(uid:int,p=Depends(administrator)):return lifecycle(uid,'restore',p)
    @router.post('/users/{uid}/delete')
    def delete(uid:int,p=Depends(administrator)):return lifecycle(uid,'delete',p)
    @router.post('/users/{uid}/reset')
    def admin_reset(uid:int,p=Depends(administrator)):
        d=env['db']()
        try:
            d.conn.execute('BEGIN IMMEDIATE');actor_current(d,p)
            row=d.conn.execute('SELECT * FROM users WHERE id=? AND active=1 AND removed_at IS NULL AND deleted_at IS NULL',(uid,)).fetchone()
            if not row:raise HTTPException(404,_t('access.not_found'))
            _,raw=a.issue_token(d,'reset',user_id=uid,actor=p['id']);url=a.link(env['ROOT'],'reset',raw)
            d.conn.execute('COMMIT')
            available=public_available();sent=available and a.send_reset(env['ROOT'],row['email'],url)
            return {'link':url,'mail_sent':bool(sent),'warnings':[],
                    'public_available':available,'public_access':available}
        finally:d.conn.close()
    @router.get('/invitations')
    def invitations(_=Depends(administrator)):
        with closing(env['db']().conn) as conn:rows=conn.execute("SELECT t.*,u.login registered_login FROM account_tokens t LEFT JOIN users u ON u.id=t.registered_user_id WHERE kind='invite' ORDER BY t.id DESC").fetchall()
        return [a.public_invitation(row) for row in rows]
    @router.post('/invitations')
    async def invite(request:Request,p=Depends(administrator)):
        from reel.web import account_mail
        data=await body(request);payload=a.fields(data.get('name'),data.get('email'),data.get('role'),data.get('groups',[]),known())
        warnings=await run_in_threadpool(account_mail.validate_email,payload['email']);d=env['db']()
        try:
            d.conn.execute('BEGIN IMMEDIATE');actor_current(d,p);assignable(d,payload['groups'])
            if a.find_user(d,payload['email']):raise HTTPException(409,_t('account_api.this_email_address_is_already_in_use'))
            existing=d.conn.execute("SELECT id FROM account_tokens WHERE kind='invite' AND used_at IS NULL AND revoked_at IS NULL AND expires_at>? AND email_identity(json_extract(payload,'$.email'))=?",(time.time(),payload['email'])).fetchone()
            if existing:raise HTTPException(409,_t('account_api.an_active_invitation_already_exists_for_this'))
            ident,raw=a.issue_token(d,'invite',payload,actor=p['id']);d.conn.execute('COMMIT')
            return await run_in_threadpool(mail_result,payload,ident,raw,warnings)
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    @router.post('/invitations/{ident}/resend')
    def resend(ident:int,p=Depends(administrator)):
        from reel.web import account_mail
        d=env['db']()
        try:
            row=d.conn.execute("SELECT * FROM account_tokens WHERE id=? AND kind='invite'",(ident,)).fetchone()
            if not row:raise HTTPException(404,_t('account_api.invitation_not_found'))
            payload=json.loads(row['payload'])
            warnings=account_mail.validate_email(payload.get('email'))
            d.conn.execute('BEGIN IMMEDIATE');actor_current(d,p)
            row=d.conn.execute("SELECT * FROM account_tokens WHERE id=? AND kind='invite'",(ident,)).fetchone()
            if not row or json.loads(row['payload'])!=payload:
                raise HTTPException(409,_t('account_api.the_invitation_has_changed_refresh_the_list'))
            if row['used_at'] is not None:raise HTTPException(409,_t('account_api.you_cannot_resend_an_invitation_that_has'))
            if a.find_user(d,payload['email']):raise HTTPException(409,_t('account_api.this_email_address_is_already_in_use'))
            payload=a.fields(payload.get('name'),payload.get('email'),payload.get('role'),payload.get('groups'),known())
            assignable(d,payload['groups'])
            d.conn.execute("UPDATE account_tokens SET revoked_at=coalesce(revoked_at,?) WHERE kind='invite' AND used_at IS NULL AND email_identity(json_extract(payload,'$.email'))=?",(time.time(),payload['email']))
            new_id,raw=a.issue_token(d,'invite',payload,actor=p['id']);d.conn.execute('COMMIT')
            return mail_result(payload,new_id,raw,warnings)
        except BaseException:
            if d.conn.in_transaction:d.conn.execute('ROLLBACK')
            raise
        finally:d.conn.close()
    @router.post('/invitations/{ident}/revoke')
    def revoke(ident:int,_=Depends(administrator)):
        with closing(env['db']().conn) as conn:
            cur=conn.execute("UPDATE account_tokens SET revoked_at=? WHERE id=? AND kind='invite' AND used_at IS NULL AND revoked_at IS NULL",(time.time(),ident))
        if cur.rowcount!=1:raise HTTPException(404,_t('account_api.active_invitation_not_found'))
        return {'ok':True}
    @router.get('/legacy')
    def legacy(_=Depends(administrator)):
        d=env['db']()
        try:return {'enabled':a.legacy_enabled(d)}
        finally:d.conn.close()
    @router.post('/legacy')
    async def set_legacy(request:Request,_=Depends(administrator)):
        data=await body(request)
        if type(data.get('enabled')) is not bool:raise HTTPException(400,_t('account_api.choose_enabled_or_disabled'))
        with closing(env['db']().conn) as conn:conn.execute("INSERT INTO account_settings VALUES('legacy_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('true' if data['enabled'] else 'false',))
        return {'ok':True}
    return router
