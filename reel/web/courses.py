"""Course-first presentation over the existing group, schedule and team records."""

from reel.i18n import t as _t
from contextlib import closing
import json,re
from pathlib import Path
from fastapi import APIRouter,HTTPException,Request
from reel.web import accounts
from reel.web.group_management import group_snapshot,save_details,eligible_people

NAMES={'ai-appdev':'courses.ai_application_development','ai-devhack':'courses.ai_and_cybersecurity','ai-graphic':'courses.graphic_design','ai-junior':'courses.ai_for_teenagers','ai-modern':'courses.modern_programming','ai-uxui':'courses.interaction_and_interface_design','conscripts-1991':'courses.programming_for_conscripts_1991','conscripts-atmk':'courses.programming_for_conscripts_atmk'}

def catalog():
    rows=json.loads((Path(__file__).resolve().parents[2]/'tools/demo_courses.json').read_text())['courses']
    return [{**r,'label':_t(NAMES[r['id']])} for r in rows]

def course_id(name):
    def norm(x):
        return ' '.join(re.sub(r'[^\w\s]',' ',x.casefold()).split())
    key=norm(name)
    if re.match(r'^ai\s+\d',key):return 'ai-modern'
    for row in sorted(catalog(),key=lambda r:len(r['name']),reverse=True):
        if key.startswith(norm(row['name'])):return row['id']
    return None

def short_group(name,ident=None):
    suffix=re.search(r'(\d+\.\d+)$',name)
    ident=ident or course_id(name)
    prefix={'ai-modern':'AI','ai-appdev':_t('courses.applications'),'ai-devhack':_t('courses.security'),'ai-graphic':_t('courses.design'),'ai-junior':_t('courses.teenagers'),'ai-uxui':_t('courses.interface'),'conscripts-1991':'1991','conscripts-atmk':_t('courses.atmk')}.get(ident)
    return (prefix+' '+suffix[1]) if suffix and prefix else name

def make_router(env):
    router=APIRouter()
    def config():
        from reel.config import load_config
        return load_config(str(env['ROOT']/'config.yaml'))
    def access(request,name):
        env['authenticated'](request);accounts.require_group(name)
        cfg=config()
        if name not in cfg['groups']:raise HTTPException(404,_t('academy_api.group_not_found'))
        return cfg['groups'][name]
    def snapshot(d,name,item):
        result=group_snapshot(d,name,item)
        for s,raw in zip(result['subjects'],item['subjects'], strict=False):s['has_playlist']=bool(raw.get('playlist'))
        linked=d.conn.execute('SELECT course_id FROM group_course_links WHERE name=?',(name,)).fetchone()
        ident=linked[0] if linked else course_id(name)
        return {**result,'short_name':short_group(name,ident),'course_id':ident}
    async def bounded(request,limit):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>limit:raise HTTPException(413,_t('account_api.request_too_large'))
        return raw
    @router.get('/api/courses')
    def courses(request:Request):
        env['authenticated'](request);cfg=config();rows=[]
        with closing(env['db']().conn) as conn:
            from types import SimpleNamespace
            for name,item in cfg['groups'].items():
                if accounts.allowed(name):rows.append(snapshot(SimpleNamespace(conn=conn),name,item))
        return {'courses':[{'id':c['id'],'name':c['label'],'groups':[r for r in rows if r['course_id']==c['id']]} for c in catalog()],
                'unassigned':[r for r in rows if not r['course_id']]}
    @router.get('/api/group-view/{name}')
    def detail(name:str,request:Request):
        item=access(request,name);d=env['db']()
        with closing(d.conn):
            result=snapshot(d,name,item)
            principal=getattr(request.state,'principal',None) or {}
            who=request.session.get('role')
            from reel.watch_statistics import allowed_groups
            allowed=allowed_groups(d,principal,known_groups=config()['groups'])
            roster=who in ('admin','lecturer') or (allowed is not None and name in allowed)
            result['can_students']=roster
            result['students']=eligible_people(d,name,'student') if roster else []
            from reel.library_view import library_entries
            result['lessons']=[r for r in library_entries(d,env['ROOT']/'library.jsonl',groups=[name])]
            if who in ('admin','lecturer'):
                actual=[env['_job_brief'](d,j) for j in d.conn.execute('SELECT * FROM jobs WHERE grp=? ORDER BY id DESC LIMIT 500',(name,))]
                result['lessons']=[*actual,*[r for r in result['lessons'] if r.get('demo')]]
            return result
    @router.put('/api/admin/group-course/{name}')
    async def assign_course(name:str,request:Request):
        env['admin'](request);access(request,name)
        raw=await bounded(request,1024)
        try:
            data=json.loads(raw)
            if set(data)!={'course_id'} or data['course_id'] not in {c['id'] for c in catalog()}:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,_t('courses.choose_a_course')) from None
        with closing(env['db']().conn) as conn:
            conn.execute('INSERT INTO group_course_links VALUES(?,?) ON CONFLICT(name) DO UPDATE SET course_id=excluded.course_id',(name,data['course_id']))
            from reel.audit import audit_connection
            audit_connection(conn,(getattr(request.state,'principal',{}) or {}).get('id'),'group.course','group:'+name,{'fields':['course']})
        return {'ok':True}
    @router.get('/api/admin/group-students/{name}')
    def picker(name:str,request:Request,q:str=''):
        env['admin'](request);access(request,name)
        if not 2<=len(q.strip())<=120:return {'items':[]}
        d=env['db']()
        with closing(d.conn):
            rows=d.conn.execute("SELECT id,name,avatar FROM users WHERE role='student' AND active=1 AND removed_at IS NULL AND deleted_at IS NULL AND EXISTS(SELECT 1 FROM json_each(groups_json) WHERE value=?) AND instr(casefold(name),?)>0 ORDER BY casefold(name),id LIMIT 20",(name,q.strip().casefold())).fetchall()
            from reel.web.group_management import _person
            return {'items':[_person(r) for r in rows]}
    @router.put('/api/admin/group-assistants/{name}')
    async def assistants(name:str,request:Request):
        env['admin'](request);item=access(request,name)
        raw=await bounded(request,4096)
        try:
            payload=json.loads(raw)
            if set(payload)!= {'ta_ids'}:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,_t('courses.choose_student_assistants')) from None
        d=env['db']()
        with closing(d.conn):
            before=group_snapshot(d,name,item)
            data={k:before[k] for k in ('course_start','course_end','ta_ids')}
            data['subjects']=[{k:s[k] for k in ('name','lecturer_id','start_date','end_date')} for s in before['subjects']]
            data['ta_ids']=payload['ta_ids']
            return save_details(d,name,item,data,actor=(getattr(request.state,'principal',{}) or {}).get('id'))
    return router
