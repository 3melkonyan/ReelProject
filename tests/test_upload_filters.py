"""G-42/2: selected-group subjects and fresh library facets, with fake JS networking/temp HTTP only."""
from contextlib import closing
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote

import pytest
import yaml

import reel
from reel.db import DB
from reel.learning import library_facets
from reel.web import accounts
from test_web_upload import web

PROJECT=Path(reel.__file__).resolve().parents[1]
NODE=Path(shutil.which('node') or 'node')


@pytest.mark.parametrize('mode',['reset','stale_success','stale_failure'])
def test_upload_subjects_reset_to_new_group_and_stale_number_results_are_ignored(mode):
    if not NODE.is_file():pytest.skip('Node is not available')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),mode=process.argv[2];
const elements={},notices=[],pending=[];let mounted=false,requests=0;
function element(){return {dataset:{},classList:{add(){},remove(){}},addEventListener(){},hidden:false,disabled:false,value:'',options:[]};}
function select(){const node=element();Object.defineProperty(node,'innerHTML',{get(){return this.html||''},set(html){this.html=html;this.options=[...html.matchAll(/<option value="([^"]*)"[^>]*>/g)].map(match=>({value:match[1]}));this.value=this.options[0]?.value||'';}});return node;}
for(const id of ['up-group','up-subject','up-course'])elements['#'+id]=select();
for(const id of ['main','upload-view','upload-form','up-number','up-date','up-files','up-files-list','up-drop'])elements['#'+id]=element();
const groups=[{name:'Group-A',subjects:[{name:'Python'},{name:'React'}]},{name:'Group-B',subjects:[{name:'Math'},{name:'Linux'}]}];
const context={console,Promise,Date,Math,JSON,Number,String,URLSearchParams,encodeURIComponent,
 S:{role:'uploader'},location:{hash:'#/upload'},navigator:{},canUpload:()=>true,lessonToday:()=> '2026-11-20',
 esc:String,ic:()=>'',toast:value=>notices.push(value),fmtSize:String,fmtDur:String,byName:()=>0,
 localStorage:{getItem:()=>null,setItem(){},removeItem(){}},window:{addEventListener(){}},
 document:{hidden:false,addEventListener(){}},setTimeout,clearTimeout,
 $:selector=>selector==='#upload-view'&&!mounted?null:elements[selector]||null,
 set:(selector,html)=>{if(selector==='#main'){elements[selector].innerHTML=html;mounted=true;}},
 api:async path=>{
   if(path==='/api/groups')return {groups};
   if(path==='/api/courses')return {courses:[{id:'all',name:'Course',groups:groups.map(x=>({name:x.name}))}]};
   if(path.startsWith('/api/uploads/next-number?')){
     if(++requests===1)return {number:1};
     return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
   }
   throw Error('Unexpected fake API path '+path);
 }};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
 await context.viewUpload();
 const group=elements['#up-group'],subject=elements['#up-subject'],number=elements['#up-number'];
 assert.deepEqual(subject.options.map(x=>x.value),['Python','React']);subject.value='React';
 if(mode==='reset'){
   group.value='Group-B';const change=group.onchange();
   assert.deepEqual(subject.options.map(x=>x.value),['Math','Linux']);assert.equal(subject.value,'Math');
   assert.match(pending[0].path,/group=Group-B&subject=Math$/);pending[0].resolve({number:7});await change;
   assert.equal(number.value,7);assert.equal(number.disabled,false);return;
 }
 const old=subject.onchange();group.value='Group-B';const latest=group.onchange();
 assert.equal(subject.value,'Math');pending[1].resolve({number:7});await latest;
 assert.equal(number.value,7);
 if(mode==='stale_failure')pending[0].reject(new Error('Old group failed'));else pending[0].resolve({number:99});
 await old;
 assert.equal(subject.value,'Math');assert.equal(number.value,7,'stale old-group response changed the current lesson number');
 assert.equal(number.disabled,false);assert.equal(notices.length,0,'stale old-group request displayed a warning');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result=subprocess.run([str(NODE),'-e',script,str(PROJECT/'reel/web/static/upload.js'),mode],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_library_facets_pure_adds_new_zero_lesson_groups_and_subjects_with_scope():
    config={'Own':{'subjects':[{'name':'Python','playlist':''}]}}
    before=library_facets([],config,(),frozenset({'Own'}))
    assert before['groups'][0]['subjects']==[{'name':'Python','lessons':0}]
    config['Own']['subjects'].append({'name':'Rust','playlist':''})
    config['New']={'subjects':[{'name':'New Subject','playlist':''}]}
    admin=library_facets([],config,(),None)
    assert {group['name'] for group in admin['groups']}=={'Own','New'}
    own=library_facets([],config,(),frozenset({'Own'}))
    assert [group['name'] for group in own['groups']]==['Own']
    assert {subject['name'] for subject in own['groups'][0]['subjects']}=={'Python','Rust'}
    assert all(subject['lessons']==0 for subject in own['groups'][0]['subjects'])


@pytest.mark.parametrize('group,foreign',[('Group-A','Python'),('Group-B','Group-A')])
def test_upload_api_rejects_another_groups_subject_before_creating_an_upload(web,group,foreign):
    client=web.client('uploader')
    code,_=client.call('POST','/api/uploads',body={'group':group,'subject':foreign,'lesson_number':13,
                                               'files':[{'name':'fixture.mov','size':8}]})
    assert code==400
    assert client.call('GET','/api/uploads/next-number?group='+quote(group)+'&subject='+quote(foreign))[0]==400
    assert not list(web.root.glob('out/uploads/*/meta.json'))


def test_library_api_refreshes_new_group_subject_and_only_the_students_own_scope(web):
    config_path=web.root/'config.yaml';config=yaml.safe_load(config_path.read_text())
    config['groups']['Group-B']['subjects'].append({'name':'Rust','playlist':''})
    config['groups']['New Group']={'subjects':[{'name':'New Subject','playlist':''}],'lecturers':[],'slack_channel':'Cnew'}
    config_path.write_text(yaml.safe_dump(config,allow_unicode=True),encoding='utf-8')
    d=DB(web.root/'reel.db')
    with closing(d.conn):
        for login,group in [('own-student','Group-B'),('new-student','New Group')]:
            accounts.create_user(d,login,'g42-filter-fixture-password',{'name':'Փորձնական ուսանող','email':login+'@example.test',
                                                                     'role':'student','groups':[group]})
    admin=web.client('admin');code,facets=admin.call('GET','/api/library/facets')
    assert code==200
    admin_groups={item['name']:item for item in facets['groups']}
    assert 'New Group' in admin_groups
    assert {'name':'Rust','lessons':0} in admin_groups['Group-B']['subjects']
    assert admin_groups['New Group']['subjects']==[{'name':'New Subject','lessons':0}]
    for login,group in [('own-student','Group-B'),('new-student','New Group')]:
        client=web.client();assert client.call('POST','/api/login',body={'login':login,'password':'g42-filter-fixture-password'})[0]==200
        code,result=client.call('GET','/api/library/facets')
        assert code==200 and [item['name'] for item in result['groups']]==[group]
        if group=='Group-B':assert {'name':'Rust','lessons':0} in result['groups'][0]['subjects']


def test_library_view_entry_refreshes_fresh_cached_facets_but_same_view_poll_reuses_ttl():
    if not NODE.is_file():pytest.skip('Node is not available')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let now=100000,facetRequests=0,lessonRequests=0;
const elements={};
function element(){return {dataset:{},isConnected:true,value:'',disabled:false,addEventListener(){}};}
function select(){const node=element();Object.defineProperty(node,'innerHTML',{get(){return this.html||''},set(html){this.html=html;this.options=[...html.matchAll(/<option value="([^"]*)"[^>]*>/g)].map(match=>({value:match[1]}));this.value=this.options[0]?.value||'';}});return node;}
const main=element();Object.defineProperty(main,'innerHTML',{set(html){
 if(!html.includes('id="library-grid"'))return;
 for(const id of ['library-grid','library-search','library-text-results','library-count'])elements['#'+id]=element();
 elements['#library-group']=select();elements['#library-subject']=select();
}});elements['#main']=main;
const own={name:'Own',lessons:0,archived:false,subjects:[{name:'Python',lessons:0}]};
let current={groups:[{...own,subjects:[...own.subjects,{name:'Rust',lessons:0}]},
 {name:'New Group',lessons:0,archived:false,subjects:[{name:'New Subject',lessons:0}]}]};
const context={S:{role:'admin',user:{id:1},libraryFacets:{groups:[own],_t:now}},
 Date:{now:()=>now},location:{hash:'#/library'},$:selector=>elements[selector]||null,
 esc:String,ic:()=>'',pageHeading:()=>'',pageSkeleton:()=>'',installLearningLifecycle(){},
 learningIdentity:()=> 'admin:1',loadLessonNotes(){},renderContinueWatching(){},renderLearningLinks(){},
 window:{addEventListener(){}},document:{body:{}},MutationObserver:class{observe(){}},
 api:async path=>{
   if(path==='/api/library'){lessonRequests++;return [];}
   if(path==='/api/library/facets'){facetRequests++;return JSON.parse(JSON.stringify(current));}
   if(path==='/api/library/progress')return {personal:true,items:[]};
   throw Error('Unexpected fake API path '+path);
 }};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.renderLibrary=()=>{};
(async()=>{
 // Returning after configuration changed: the old facets are only one millisecond old.
 now++;await context.viewLibrary();
 assert.equal(facetRequests,1,'a newly created library view reused the previous visit\'s facets');
 assert(elements['#library-group'].options.some(option=>option.value==='New Group'));
 assert(elements['#library-subject'].options.some(option=>option.value==='Rust'));
 now++;await context.viewLibrary();
 assert.equal(facetRequests,1,'same-view polling bypassed the 30-second facets cache');
 assert.equal(lessonRequests,2,'same-view polling should continue refreshing lessons');
 // A second route entry must refresh again even though the preceding fetch is still fresh.
 elements['#library-grid'].isConnected=false;delete elements['#library-grid'];
 current.groups.push({name:'Another New Group',lessons:0,archived:false,subjects:[]});
 now++;await context.viewLibrary();
 assert.equal(facetRequests,2);
 assert(elements['#library-group'].options.some(option=>option.value==='Another New Group'));
 now+=30001;await context.viewLibrary();
 assert.equal(facetRequests,3,'the ordinary same-view TTL refresh stopped working');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result=subprocess.run([str(NODE),'-e',script,str(PROJECT/'reel/web/static/insights.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
