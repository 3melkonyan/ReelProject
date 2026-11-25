"""Read-only academy presentation data; real production and illustrative metadata stay separate."""
from __future__ import annotations

from reel.i18n import t as _t

from collections import Counter
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from reel import insights
from reel.lesson_dates import job_date
from reel.web.group_status import completed_groups

ZONE = ZoneInfo('Asia/Yerevan')
STATUSES = ('published','processing','queued','stopped','done','demo')
_PUBLIC = ('id','job_id','title','group','subject','lesson_date','date_inferred','status','job_status',
           'published','demo','playable','thumbnail','url','detail_url','duration_sec')
PLACEHOLDER = '/brand/reel-email.png'


def _number(value):
    if isinstance(value,bool):return 0.0
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return 0.0
    return value if math.isfinite(value) and value>0 else 0.0


def _day(now=None):
    if now is None:return datetime.now(ZONE).date()
    if isinstance(now,datetime):return (now.replace(tzinfo=ZONE) if now.tzinfo is None else now).astimezone(ZONE).date()
    return now if isinstance(now,date) else date.fromisoformat(now)


def _table(database,name):
    return database.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def _permission(database,principal):
    if not isinstance(principal,dict):raise PermissionError('personal or permitted legacy account required')
    if principal.get('legacy'):
        if principal.get('role') not in ('viewer','uploader'):raise PermissionError('operational account required')
        return None,False
    if type(principal.get('id')) is not int:raise PermissionError('personal account required')
    row=database.conn.execute('SELECT * FROM users WHERE id=?',(principal['id'],)).fetchone()
    if not row:raise PermissionError('account not found')
    user=dict(row)
    if not user.get('active') or user.get('removed_at') or user.get('deleted_at'):raise PermissionError('inactive account')
    if user['role']=='admin':return None,True
    if user['role']!='lecturer':raise PermissionError('operational account required')
    try:groups=json.loads(user['groups_json'])
    except (TypeError,ValueError):groups=[]
    return frozenset(group for group in groups if isinstance(group,str)) if isinstance(groups,list) else frozenset(),False


def _thumbnail(root,ident,privacy):
    if privacy:
        folder=Path(root)/'work';path=folder/str(ident)/'thumbnail.jpg'
        if not folder.is_symlink() and not path.parent.is_symlink() and not path.is_symlink() and path.is_file():
            return f'/api/jobs/{ident}/thumbnail'
    return PLACEHOLDER


def _demo_thumbnail(value):
    if (isinstance(value,str) and value.startswith('/brand/') and '\\' not in value
            and '..' not in Path(value).parts and not any(ord(char)<32 for char in value)):
        return value
    return PLACEHOLDER


def _data(database,config,root,principal):
    scope,administrator=_permission(database,principal)
    jobs=[dict(row) for row in database.conn.execute('SELECT * FROM jobs ORDER BY id DESC')
          if scope is None or row['grp'] in scope]
    published={row['job_id']:row for row in insights.library_records(database,Path(root)/'library.jsonl',groups=scope)}
    rows=[]
    for job in jobs:
        ident=job['id'];publication=published.get(ident);meta=database.stage_output(ident,'metadata') or {}
        plan=database.stage_output(ident,'segments') or {};probe=database.stage_output(ident,'probe') or {}
        privacy=database.stage(ident,'privacy')
        passed=bool(privacy and privacy['status']=='done')
        title=(publication or {}).get('title') or meta.get('title') or Path(job['source_path']).name or _t('ui.app.lesson')
        raw=job['status']
        state='processing' if raw=='running' else raw
        if raw=='done' and publication:state='published'
        if state not in STATUSES:state='done'
        rows.append({'id':f'job:{ident}','job_id':ident,'title':str(title),'group':job['grp'],'subject':(publication or {}).get('subject') or job['subject'] or '',
                     'lesson_date':job_date(job),'date_inferred':not bool(job.get('lesson_date')),
                     'status':state,'job_status':raw,'published':bool(publication),'demo':False,'playable':bool(publication),
                     'thumbnail':publication['thumbnail'] if publication else _thumbnail(root,ident,passed),
                     'url':publication['url'] if publication else None,'detail_url':f'#/jobs/{ident}',
                     'duration_sec':_number(publication.get('duration')) if publication else _number(plan.get('final_sec')),
                     '_source_sec':_number(probe.get('duration_sec')) or _number(plan.get('original_sec'))})
    if _table(database,'demo_lessons'):
        from reel.demo import read_demo_lessons
        for item in read_demo_lessons(database,groups=scope):
            rows.append({'id':'demo:'+str(item['id']),'job_id':None,'title':str(item.get('title') or _t('text.academy.demo_lesson')),
                         'group':item['group'],'subject':item.get('subject') or '',
                         'lesson_date':item.get('lesson_date'),'date_inferred':False,'status':'demo','job_status':None,
                         'published':False,'demo':True,'playable':False,'thumbnail':_demo_thumbnail(item.get('thumbnail')),
                         'url':None,'detail_url':None,'duration_sec':_number(item.get('duration_sec')),'_source_sec':0.0})
    rows.sort(key=lambda item:(str(item.get('lesson_date') or ''),item['job_id'] if item['job_id'] is not None else -1,item['id']),reverse=True)
    return rows,jobs,scope,administrator


def _public(row):
    return {key:row.get(key) for key in _PUBLIC}


def _filter_values(rows,config,scope,group=None,subject=None):
    names={name for name in config.get('groups',{}) if scope is None or name in scope}
    names.update(row['group'] for row in rows)
    if group and (len(group)>120 or group not in names):raise LookupError('group not found')
    if subject and len(subject)>120:raise ValueError('subject filter too long')
    counts=Counter(row['group'] for row in rows)
    pairs=Counter((row['group'],row['subject']) for row in rows)
    groups=[]
    for name in sorted(names,key=str.casefold):
        configured=[item['name'] for item in config.get('groups',{}).get(name,{}).get('subjects',[])]
        choices=set(configured)|{label for (grp,label) in pairs if grp==name and label}
        groups.append({'name':name,'count':counts[name],
                       'subjects':[{'name':label,'count':pairs[(name,label)]} for label in sorted(choices,key=str.casefold)]})
    selected=[item for item in groups if not group or item['name']==group]
    labels={item['name'] for grp in selected for item in grp['subjects']}
    flat=[{'name':label,'count':sum(pairs[(grp['name'],label)] for grp in selected)} for label in sorted(labels,key=str.casefold)]
    return {'groups':groups,'subjects':flat}


def lessons(database,config,root,principal,*,group=None,subject=None,status='all',now=None):
    rows,_,scope,_=_data(database,config,root,principal)
    status=status or 'all'
    if status not in ('all',*STATUSES):raise ValueError('unknown lesson status')
    filters=_filter_values(rows,config,scope,group,subject)
    filters['statuses']=[{'value':state,'count':sum(row['status']==state for row in rows)} for state in STATUSES]
    items=[_public(row) for row in rows if (not group or row['group']==group)
           and (not subject or row['subject']==subject) and (status=='all' or row['status']==status)]
    return {'items':items,'total':len(items),'filters':filters,
            'selected':{'group':group or None,'subject':subject or None,'status':status}}


def overview(database,config,root,principal,*,now=None):
    rows,jobs,scope,administrator=_data(database,config,root,principal)
    today=_day(now);start=today-timedelta(days=today.weekday());end=start+timedelta(days=6)
    done=completed_groups(database,today=today)
    demo_groups=set()
    if _table(database,'group_metadata'):
        demo_groups={row['name'] for row in database.conn.execute("SELECT name FROM group_metadata WHERE demo_tag IS NOT NULL AND demo_tag!=''")}
    active={name for name in config.get('groups',{}) if name not in done and (scope is None or name in scope)}
    weekly=[row for row in rows if row.get('lesson_date') and start.isoformat()<=row['lesson_date']<=end.isoformat()]
    actual={'active_groups':len(active-demo_groups),'week_lessons':sum(not row['demo'] for row in weekly),
            'processing':sum(job['status']=='running' for job in jobs),'queued':sum(job['status']=='queued' for job in jobs),
            'published_lessons':sum(row['published'] for row in rows)}
    demo={'active_groups':len(active&demo_groups),'week_lessons':sum(row['demo'] for row in weekly),
          'lessons':sum(row['demo'] for row in rows),'processing':0,'queued':0,'published_lessons':0}
    result={'cards':{**actual,'active_groups':len(active),'week_lessons':len(weekly)},'actual':actual,'demo':demo,
            'latest':[_public(row) for row in rows if row['published'] or row['demo']][:6],
            'processing':[_public(row) for row in rows if row['job_status'] in ('queued','running')][:6],
            'week_start':start.isoformat(),'week_end':end.isoformat()}
    if administrator:
        stopped=[job['id'] for job in jobs if job['status']=='stopped']
        unpublished=[row['job_id'] for row in rows if row['job_status']=='done' and not row['published']]
        result['warnings']=[]
        if stopped:result['warnings'].append({'kind':'stopped','count':len(stopped),'job_ids':stopped,
                                             'message':_t('text.academy.some_lessons_need_attention'),'href':'#/jobs?status=stopped'})
        if unpublished:result['warnings'].append({'kind':'unpublished','count':len(unpublished),'job_ids':unpublished,
                                                 'message':_t('text.academy.some_finished_lessons_have_unconfirmed_publication_status'),'href':'#/jobs?status=done'})
    return result


def _assumptions(config):
    raw=config.get('time_savings') or {}
    return {key:_number(raw[key]) if key in raw and type(raw[key]) in (int,float) and raw[key]>=0 else value
            for key,value in insights.SAVINGS.items()}


def _metrics(rows,assumptions,demo=False):
    selected=[row for row in rows if row['demo']==demo and (demo or row['published'])]
    duration=sum(row['duration_sec'] for row in selected)
    saved=(sum(row['_source_sec'] for row in selected)/3600*assumptions['editing_min_per_hour']
           +len(selected)*assumptions['publishing_min_per_lesson']) if not demo else 0.0
    return {('lessons' if demo else 'published_lessons'):len(selected),
            'video_hours':round(duration/3600,3),'saved_minutes':round(saved,1)}


class _SelectedConnection:
    """The existing technical aggregator iterates this explicit read-only job selection."""
    def __init__(self,connection,jobs):self.connection,self.jobs=connection,jobs
    def execute(self,query,parameters=()):
        if query=='SELECT * FROM jobs':return iter(self.jobs)
        return self.connection.execute(query,parameters)


class _SelectedDatabase:
    def __init__(self,database,jobs):self.database,self.conn=database,_SelectedConnection(database.conn,jobs)
    def __getattr__(self,name):return getattr(self.database,name)


def statistics(database,config,root,principal,*,group=None,subject=None,now=None):
    rows,jobs,scope,administrator=_data(database,config,root,principal)
    available=[row for row in rows if row['published'] or row['demo']]
    filters=_filter_values(available,config,scope,group,subject)
    selected=[row for row in available if (not group or row['group']==group) and (not subject or row['subject']==subject)]
    assumptions=_assumptions(config)
    def aggregate(items):
        return {'actual':_metrics(items,assumptions),'demo':_metrics(items,assumptions,True),'lessons':len(items)}
    actual,demo=_metrics(selected,assumptions),_metrics(selected,assumptions,True)
    by_group=[];by_subject=[]
    for option in filters['groups']:
        name=option['name']
        if group and name!=group:continue
        matching=[row for row in selected if row['group']==name]
        by_group.append({'group':name,**aggregate(matching)})
        for item in option['subjects']:
            label=item['name']
            if subject and label!=subject:continue
            by_subject.append({'group':name,'subject':label,**aggregate([row for row in matching if row['subject']==label])})
    today=_day(now);daily=[]
    for offset in range(29,-1,-1):
        day=(today-timedelta(days=offset)).isoformat();items=[row for row in selected if row['lesson_date']==day]
        actual_day=_metrics(items,assumptions);demo_day=_metrics(items,assumptions,True)
        daily.append({'date':day,'actual_lessons':actual_day['published_lessons'],'demo_lessons':demo_day['lessons'],
                      'actual_hours':actual_day['video_hours'],'demo_hours':demo_day['video_hours']})
    result={'cards':actual,'actual':actual,'demo':demo,'by_group':by_group,'by_subject':by_subject,'daily':daily,
            'filters':filters,'assumptions':assumptions,'date_basis':'lesson_date',
            'period_start':daily[0]['date'],'period_end':daily[-1]['date']}
    if administrator:
        presented={row['job_id']:row for row in rows if row['job_id'] is not None}
        filtered=[job for job in jobs if (not group or job['grp']==group)
                  and (not subject or presented[job['id']]['subject']==subject)]
        result['technical']=insights.statistics(_SelectedDatabase(database,filtered),assumptions,groups=scope)
    return result
