"""Only temporary SQLite/config/media; no real uploads or worker."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from reel.db import DB
from reel.insights import cut_summary, library_records, statistics
from reel.progress import Tracker, byte_fraction, ffmpeg_seconds, weights
from reel.config import validate_config



def seed(root):
    db = DB(root/'reel.db')
    job = db.create_job('/fixture/Դաս 7.mp4', 'g34-'+str(time.monotonic_ns()), 'Academy', 'Python')
    plan = {'keep':[[20,500],[600,990]], 'cuts':[{'start':0,'end':20,'why':'before_class'},
            {'start':500,'end':600,'why':'break'}, {'start':990,'end':1000,'why':'after_class'}],
            'original_sec':1000,'final_sec':870,'cut_ratio':.13}
    transcript = {'segments':[{'start':1,'end':10,'text':'նախնական զրույց առաջին բառերը'},
                              {'start':510,'end':530,'text':'ընդմիջման խոսակցություն'}]}
    plan['cut_summary'] = cut_summary(plan,transcript,{'parts':2,'part_sec':[500,500]})
    outputs = {'probe':{'duration_sec':1000},'segments':plan,'metadata':{'title':'Ֆունկցիաներ',
              'chapters':[{'t':120,'final_t':100,'title':'Տեսանելիության տիրույթ'}]},
              'youtube_upload':{'lesson_no':7},'blur':{'blurred_boxes':130,'student_tracks':2},'privacy':{}}
    for name, output in outputs.items():
        db.stage_start(job,name);db.stage_done(job,name,output)
        db.conn.execute("UPDATE stages SET started_at=?,finished_at=? WHERE job_id=? AND name=?",
                        ('2026-01-01T00:00:00+00:00','2026-01-01T00:01:00+00:00',job,name))
    db.add_publication(job,'youtube','abcdefghijk','https://youtu.be/abcdefghijk')
    db.set_job(job,status='done');return db,job,plan


def test_cleanup_safe_library_chapters_and_statistics(tmp_path):
    db,job,plan=seed(tmp_path)
    records=library_records(db)
    assert records[0]['chapters'][0]['url']=='https://youtu.be/abcdefghijk?t=100'
    assert records[0]['lesson_no']==7
    assert not (tmp_path/'work').exists()
    data=statistics(db)
    assert data['cut_sec']==130 and data['blurred_boxes']==130 and data['student_tracks']==2
    assert data['published_sec']==870 and data['counts']['done']==1
    assert data['saved_minutes']==pytest.approx(1000/3600*30+5)
    assert plan['cut_summary']['cuts'][1]['final_boundary']==480
    assert plan['cut_summary']['parts']['parts']==2
    assert plan['cut_summary']['cuts'][0]['first_words'].startswith('նախնական')
    db.conn.close()


def test_legacy_library_missing_values_and_privacy(tmp_path):
    db,job,_=seed(tmp_path)
    db.conn.execute("DELETE FROM stages WHERE name IN ('metadata','segments','youtube_upload')")
    legacy=tmp_path/'library.jsonl';legacy.write_text(json.dumps({'job_id':str(job),'title':'Old title'})+'\n')
    entry=library_records(db,legacy)[0]
    assert entry['title']=='Old title' and entry['duration'] is None and entry['chapters']==[]
    db.conn.execute("DELETE FROM stages WHERE name='privacy'")
    assert library_records(db,legacy)==[]
    db.conn.close()


@pytest.mark.parametrize('sent,total,expected',[(50,100,.5),(200,100,1),(-1,10,0),(1,0,None),(float('nan'),2,None)])
def test_bytes(sent,total,expected): assert byte_fraction(sent,total)==expected


@pytest.mark.parametrize('text,value',[('out_time_ms=12500000',12.5),('out_time_us=2000000',2),
                                       ('out_time=01:02:03.500',3723.5),('out_time=N/A',None)])
def test_ffmpeg_parser(text,value): assert ffmpeg_seconds(text)==value


def test_frozen_median_weights_monotonic_eta_and_only_done_100(tmp_path):
    db,old,_=seed(tmp_path)
    w=weights(db,2000,['probe','blur','slack_post'])
    assert w['blur']==pytest.approx(120) and w['slack_post']>=300
    job=db.create_job('/fixture/new.mp4','new','Academy')
    db.stage_start(job,'probe');db.stage_done(job,'probe',{'duration_sec':2000})
    db.stage_start(job,'blur')
    tracker=Tracker(db,job,['probe','blur','slack_post'])
    with tracker.scope('blur'):
        tracker.began-=60;tracker.last_write-=2;tracker.observe(.5,force=True)
        first=db.insights(job)['progress'];assert first['eta_seconds']==pytest.approx(405,abs=.1)
        assert first['percent']==pytest.approx(100/3)
        tracker.last_write-=2;tracker.observe(.1,force=True)
        second=db.insights(job)['progress'];assert second['percent']==first['percent']
        assert second['weights']==first['weights']
        tracker.last_write-=2;tracker.observe(1,force=True);assert db.insights(job)['progress']['percent']<100
    db.set_job(job,status='done');tracker.observe(1,force=True)
    assert db.insights(job)['progress']['percent']==100
    assert 'eta_error_sec' in db.insights(job)['progress']
    db.conn.close()


def test_observer_throttled_and_migration_idempotent(tmp_path):
    db=DB(tmp_path/'reel.db');job=db.create_job('x','x','x')
    tracker=Tracker(db,job,['probe']);calls=[]
    save=db.save_insights
    db.save_insights=lambda *args:(calls.append(args),save(*args))
    with tracker.scope('probe'):
        for i in range(1000):tracker.observe(i/1000)
    with tracker.scope('audio'):
        tracker.observe(1,force=True)
    assert len(calls)==1  # forced short-stage transitions are coalesced too
    DB(tmp_path/'reel.db').conn.close();db.conn.close()


def test_unclear_no_cut_reason_is_honest():
    data=cut_summary({'keep':[[0,100]],'cuts':[],'original_sec':100,'final_sec':100,'auto_note':'plan failed'})
    assert data['no_cuts_reason']=='Կտրում չի կատարվել։'


@pytest.mark.parametrize('key,value',[('editing_min_per_hour',-1),('editing_min_per_hour',float('nan')),
                                    ('publishing_min_per_lesson',True),('publishing_min_per_lesson',121)])
def test_savings_validation(key,value):
    cfg={'groups':{'A':{'subjects':[{'name':'Python','playlist':''}],'slack_channel':'C'}},
         'operator_slack_channel':'C','cut':{'min_silence_sec':180,'max_cut_ratio':.4},
         'time_savings':{'editing_min_per_hour':30,'publishing_min_per_lesson':5}}
    cfg['time_savings'][key]=value
    with pytest.raises(ValueError):validate_config(cfg)


def test_http_roles_cleanup_and_preview_guards(web):
    db,job,_=seed(web.root);db.conn.close()
    student=web.client();password=json.loads(web.secrets.read_text())['student_password']
    assert student.call('POST','/api/login',body={'password':password})==(200,{'role':'student'})
    assert student.call('GET','/api/me')[1]=={'role':'student'}
    assert student.call('GET','/api/library')[0]==200
    for route in ['/api/jobs','/api/summary','/api/groups',f'/api/jobs/{job}',f'/api/jobs/{job}/cuts',
                  '/api/stats','/api/uploads/next-number?group=Academy','/api/admin/config',f'/api/jobs/{job}/video']:
        assert student.call('GET',route)[0] in (401,403),route
    anonymous=web.client();assert anonymous.call('GET','/api/library')[0]==401
    uploader=web.client('uploader')
    assert uploader.call('GET','/api/library')[0]==403
    assert uploader.call('GET',f'/api/jobs/{job}/cuts')[0]==403
    viewer=web.client('viewer');base=f'/api/jobs/{job}/cuts'
    assert viewer.call('GET',base)[1]['cuts'][1]['first_words']=='ընդմիջման խոսակցություն'
    assert viewer.call('GET',f'/api/jobs/{job}')[1]['metadata']['title']=='Ֆունկցիաներ'
    assert viewer.call('GET','/api/stats')[1]['source_sec']==1000
    assert viewer.call('POST',base+'/0/preview',csrf=False)[0]==400
    assert viewer.call('POST',base+'/0/preview')[0]==410
    assert viewer.call('GET','/api/jobs/not-an-int/cuts')[0]==422
    media=web.root/'work'/str(job);media.mkdir(parents=True)
    (media/'final_blurred.mp4').symlink_to(web.root/'config.yaml')
    assert viewer.call('POST',base+'/0/preview')[0]==403


def test_ffmpeg_observation_preserves_result_and_command(monkeypatch):
    from reel import progress
    observed=[]
    class Sink:
        duration=20;stage='preview'
        def observe(self,fraction):observed.append(fraction)
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            self.args=args;self.calls=0
            Path(args[args.index('-progress')+1]).write_text('out_time_us=10000000\n')
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def communicate(self,timeout=None):
            self.calls+=1
            if self.calls==1:raise subprocess.TimeoutExpired(self.args,2)
            return b'output',b'stderr'
    monkeypatch.setattr(progress.subprocess,'Popen',Process)
    token=progress._ACTIVE.set(Sink())
    try:
        result=progress.observed_run(['ffmpeg','-i','input','output.mp4'],capture_output=True,check=True)
        assert result.stdout==b'output' and result.stderr==b'stderr'
        assert result.args==['ffmpeg','-i','input','output.mp4'] and observed==[.5]
    finally:progress._ACTIVE.reset(token)


def test_http_preview_cache_single_encode_and_timeout(web,monkeypatch):
    from reel.web import insights
    db,job,_=seed(web.root);db.conn.close()
    media=web.root/'work'/str(job);media.mkdir(parents=True)
    final=media/'final_blurred.mp4';final.write_bytes(b'blurred-final-fixture')
    calls=[]
    def encode(command,**kwargs):
        calls.append((command,kwargs));Path(command[-1]).write_bytes(b'synthetic-safe-preview')
    monkeypatch.setattr(insights.subprocess,'run',encode)
    c=web.client('viewer');base=f'/api/jobs/{job}/cuts'
    assert c.call('POST',base+'/0/preview')[0]==200
    assert c.call('POST',base+'/0/preview')[0]==200 and len(calls)==1
    assert c.call('GET',base+'/0/preview')==(200,b'synthetic-safe-preview')
    command,options=calls[0]
    assert str(final) in command and 'final.mp4' not in command
    assert command[:3]==['nice','-n','15'] and options['timeout']==90
    assert command[command.index('-vf')+1]=='scale=-2:480'
    assert command[command.index('-c:v')+1]=='libx264'
    insights._ENCODE.acquire()
    try:assert c.call('POST',base+'/1/preview')[0]==409
    finally:insights._ENCODE.release()
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired(args[0],90)
    monkeypatch.setattr(insights.subprocess,'run',timeout)
    assert c.call('POST',base+'/1/preview')[0]==504
    assert not list((web.root/'out/transition-previews').rglob('*.part.mp4'))
    assert c.call('GET',base+'/99/preview')[0]==404
    db=DB(web.root/'reel.db');db.conn.execute("DELETE FROM stages WHERE name='privacy'");db.conn.close()
    assert c.call('GET',base+'/0/preview')[0]==403


def test_http_savings_admin_validation(web,monkeypatch):
    from reel.web import admin
    monkeypatch.setattr(admin,'ROOT',web.root)
    c=web.client('admin');body={'operator_slack_channel':'Cowner','cut':{'min_silence_sec':180,'max_cut_ratio':.4},
                              'time_savings':{'editing_min_per_hour':20,'publishing_min_per_lesson':3}}
    assert c.call('PUT','/api/admin/settings',body=body,csrf=False)[0]==400
    assert c.call('PUT','/api/admin/settings',body=body)[0]==200
    assert c.call('GET','/api/stats')[1]['assumptions']==body['time_savings']
    body['time_savings']['editing_min_per_hour']=-1
    assert c.call('PUT','/api/admin/settings',body=body)[0]==400
    assert web.client('viewer').call('PUT','/api/admin/settings',body=body)[0]==403


def test_legacy_cut_details_are_unknown_not_zero():
    data=cut_summary({'original_sec':1000,'final_sec':900,'keep':[[100,1000]]})
    assert data['details_available'] is False
    assert 'պահպանված չեն' in data['no_cuts_reason']


def test_ui_progress_observed_ceiling_and_student_navigation():
    from test_brand import run_node
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'student'},esc:String,ic:()=>'',isDark:()=>false};
c.canUpload=()=>false;vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(source.slice(source.indexOf('const navLink ='),source.indexOf('function route()')),c);
const html=c.shell('library');assert(html.includes('href="#/library"'));
for(const forbidden of ['jobs','stats','upload','settings'])assert(!html.includes('href="#/'+forbidden+'"'));
vm.runInContext(source.slice(source.indexOf('const pct ='),source.indexOf('const badge ='))+'globalThis.percent=pct;',c);
assert.equal(c.percent({status:'running',progress:{percent:100}}),99);
assert.equal(c.percent({status:'done',progress:{percent:80}}),100);
assert.equal(c.percent({status:'running',done_stages:8,total_stages:10}),0);
const queue=[], values=[];let observer, reduced=false, el={dataset:{progressJob:'1',progressValue:'37'},isConnected:true};
const ui={window:{matchMedia:()=>({matches:reduced}),addEventListener:()=>{}},document:{querySelectorAll:()=>[el],body:{}},
 MutationObserver:class {constructor(fn){observer=fn}observe(){}},setTimeout:fn=>queue.push(fn),Map};
vm.createContext(ui);require(require('path').resolve('tests/browser_locale.cjs')).install(ui);vm.runInContext(fs.readFileSync(process.argv[1].replace('app.js','insights.js'),'utf8'),ui);
observer();assert.equal(el.textContent,'37%');el={dataset:{progressJob:'1',progressValue:'45'},isConnected:true};observer();
values.push(el.textContent);while(queue.length){queue.shift()();values.push(el.textContent)}
assert.deepEqual(values,['38%','39%','40%','41%','42%','43%','44%','45%']);
reduced=true;el={dataset:{progressJob:'1',progressValue:'60'},isConnected:true};observer();assert.equal(el.textContent,'60%');assert.equal(queue.length,0);
''')


def test_library_stage_retains_legacy_return_and_enriches_record(tmp_path,monkeypatch):
    from reel import stages
    from reel.pipeline import Ctx
    from reel.publish import library
    db,job,_=seed(tmp_path)
    monkeypatch.setattr(stages,'_subject',lambda ctx:{'name':'Python'})
    monkeypatch.setattr(stages,'_pub',lambda name:library)
    monkeypatch.setattr(library,'_path',lambda:tmp_path/'library.jsonl')
    ctx=Ctx(db,job,tmp_path/'source.mp4',tmp_path,'Academy',{})
    db.stage_start(job,'youtube_upload');db.stage_done(job,'youtube_upload',{'url':'https://youtu.be/abcdefghijk','lesson_no':7})
    result=stages.library_add(ctx)
    assert result=={'external_id':None,'url':None,'reused':False}
    entry=json.loads((tmp_path/'library.jsonl').read_text())
    assert entry['subject']=='Python' and entry['lesson_no']==7 and entry['duration']==870
    assert entry['chapters'][0]['final_t']==100
    db.conn.close()


def test_student_migration_keeps_passwords_and_env_is_not_persisted(web,monkeypatch):
    module=next(m for m in list(sys.modules.values()) if getattr(m,'__name__','').startswith('_reel_upload_web_') and getattr(m,'ROOT',None)==web.root)
    data=json.loads(web.secrets.read_text());data.pop('student_password')
    web.secrets.write_text(json.dumps(data))
    monkeypatch.setenv('REEL_STUDENT_PASSWORD','runtime-student-password')
    loaded=module._load_secrets();saved=json.loads(web.secrets.read_text())
    assert loaded['student_password']=='runtime-student-password'
    assert saved['student_password']!='runtime-student-password'
    assert {k:saved[k] for k in data}==data
    before=web.secrets.read_bytes();module._load_secrets();assert web.secrets.read_bytes()==before


def test_progress_parsing_cost_is_bounded_and_unknown_probe_is_safe(tmp_path):
    from reel.progress import read_progress,media_seconds
    path=tmp_path/'progress';path.write_text('noise\n'*10000+'out_time_us=4000000\n')
    tail=read_progress(path)
    assert len(tail.encode())<=8192 and ffmpeg_seconds(tail)==4
    assert media_seconds({'format':{'duration':'N/A'}})==0
    assert media_seconds({'format':{'duration':'NaN'}})==0
