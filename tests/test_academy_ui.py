"""Academy cards preserve non-playable demos and role-safe summaries."""
from test_brand import run_node

HOME = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync(process.argv[1],'utf8'),out=[];
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mainHost={};const c={S:{role:'admin'},$:()=>mainHost,location:{hash:'#/'},esc,ic:()=>'',formatLessonDate:x=>x||'Նշված չէ',pageHeading:(title,desc,action)=>`<h1>${title}</h1><p>${desc}</p>${action}`,canUpload:()=>true,stat:(i,t,k,v,s)=>`<div>${k}:${v}:${s}</div>`,set:(sel,html)=>out.push(html)};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(src.slice(src.indexOf('const ACADEMY_STATUS'),src.indexOf('const feedRow')),c);
'''


def test_academy_demo_never_links_to_player_and_escapes_metadata():
    run_node(HOME + r'''
const demo=c.academyLessonCard({demo:true,title:'<img onerror=bad>',group:'AI',subject:'Math',status:'demo',lesson_date:'2026-11-21',thumbnail:'/brand/demo.svg',detail_url:'#/jobs/99',published:true,playable:true,job_id:99});
assert(!demo.includes('<a '));assert(demo.includes('Ցուցադրական'));assert(demo.includes('Առանց տեսանյութի'));assert(demo.includes('&lt;img onerror=bad&gt;'));
const published=c.academyLessonCard({title:'Դաս',group:'AI',published:true,playable:true,job_status:'done',job_id:7,status:'published'});assert(published.includes('href="#/library/7"'));
const pending=c.academyLessonCard({title:'Դաս',group:'AI',job_id:8,status:'queued',detail_url:'#/jobs/8'});assert(pending.includes('href="#/jobs/8"'));assert(!pending.includes('#/library/'));
''')


def test_dashboard_summary_counts_demos_and_hides_warnings_for_non_admin():
    run_node(HOME + r'''
const d={cards:{active_groups:2,week_lessons:4,processing:1,queued:2,published_lessons:3},actual:{week_lessons:1},demo:{active_groups:1,week_lessons:3},latest:[],processing:[],warnings:[{href:'#/jobs?status=stopped',message:'Admin warning',count:1}]};
c.api=async path=>{assert.equal(path,'/api/academy/overview');return d;};
(async()=>{await c.viewHome();let html=out.pop();assert(html.includes('Ակտիվ խմբեր:2'));assert(html.includes('Այս շաբաթվա դասեր:4'));assert(html.includes('3 ցուցադրական'));assert(html.includes('Admin warning'));assert(html.includes('Վերբեռնել առաջին դասը'));
c.S.role='lecturer';await c.viewHome();html=out.pop();assert(!html.includes('Admin warning'));assert(!html.includes('Ուշադրության կարիք ունի</h2>'));})();
''')


def test_classes_filters_are_sent_to_server_and_old_responses_do_not_replace_new_page():
    run_node(HOME + r'''
let host={},received='',block=false,release;
c.S={role:'lecturer',jobGroup:'AI 2.6',jobSubject:'Math',jobFilter:'demo',jobSearch:'ՀԱՆՐԱ'};
c.$=selector=>selector==='#jobs-list'?host:{};c.location={hash:'#/jobs?group=AI+2.6&subject=Math&status=demo'};
c.URLSearchParams=URLSearchParams;c.history={replaceState:(a,b,url)=>received=url};
c.api=async path=>{received=path;if(block)await new Promise(resolve=>release=resolve);return {items:[{title:'Գծային հանրահաշիվ',group:'AI 2.6',subject:'Math',demo:true,status:'demo'}],filters:{groups:[{name:'AI 2.6',count:1}],subjects:[{name:'Math',count:1}]}};};
vm.runInContext(src.slice(src.indexOf('function saveJobFilters()'),src.indexOf('async function viewGroups()')),c);
(async()=>{await c.viewJobs();assert(received.includes('group=AI+2.6'));assert(received.includes('subject=Math'));assert(received.includes('status=demo'));
assert(out.at(-1).includes('Գծային հանրահաշիվ'));assert(!out.at(-1).includes('<a '));
c.saveJobFilters();assert(received.startsWith('#/jobs?'));assert(received.includes('q='));
block=true;const n=out.length,pending=c.viewJobs();await new Promise(resolve=>setTimeout(resolve,0));host={};release();await pending;assert.equal(out.length,n);})();
''')


def test_dashboard_ignores_response_after_account_or_page_change():
    run_node(HOME + r'''
let release;c.api=()=>new Promise(resolve=>release=resolve);
const d={cards:{},demo:{},actual:{},latest:[],processing:[]};
(async()=>{const pending=c.viewHome();c.S.role='student';release(d);await pending;assert.equal(out.length,0);
c.S.role='admin';const next=c.viewHome();c.location.hash='#/jobs';release(d);await next;assert.equal(out.length,0);
const item={title:'Դաս',published:true,playable:true,job_status:'done',job_id:7,detail_url:'#/jobs/7',status:'published'};
c.S.role='uploader';assert(c.academyLessonCard(item).includes('href="#/jobs/7"'));
c.S.role='admin';item.job_status='stopped';assert(c.academyLessonCard(item).includes('href="#/jobs/7"'));})();
''')


def test_old_running_filter_link_selects_current_processing_state():
    run_node(HOME + r'''
let host=null,requested='';const main={set innerHTML(value){host={};}};
c.$=selector=>selector==='#jobs-list'?host:selector==='#main'?main:{};
c.location.hash='#/jobs?status=running';c.URLSearchParams=URLSearchParams;c.pageSkeleton=()=>'<div></div>';
c.api=async path=>{requested=path;return {items:[],filters:{groups:[],subjects:[]}};};
vm.runInContext(src.slice(src.indexOf('function saveJobFilters()'),src.indexOf('async function viewGroups()')),c);
(async()=>{await c.viewJobs();assert.equal(c.S.jobFilter,'processing');assert(requested.includes('status=processing'));assert(!requested.includes('status=running'));})();
''')
