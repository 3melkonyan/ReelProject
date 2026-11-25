"""Shared headings/navigation/status, with pure mocked JavaScript only."""
from test_player_ui import DOM
from test_brand import run_node


SHELL = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'admin',user:null},isDark:()=>false,canUpload:()=>false};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(source.slice(source.indexOf('const ICONS ='),source.indexOf('const STATUS =')),c);
vm.runInContext(source.slice(source.indexOf('const navLink ='),source.indexOf('function route()')),c);
'''


def test_shared_heading_escapes_text_and_keeps_one_title_description_and_action():
    run_node(SHELL + r'''
const html=c.pageHeading('<Title>','A < B','<a class="btn primary" href="#/upload">Վերբեռնել</a>');
assert.equal((html.match(/<h1>/g)||[]).length,1);assert.equal((html.match(/<p>/g)||[]).length,1);
assert(html.includes('&lt;Title&gt;'));assert(html.includes('A &lt; B'));assert(!html.includes('eyebrow'));
assert.equal((html.match(/btn primary/g)||[]).length,1);assert(html.includes('page-actions'));
const loading=c.pageSkeleton('Բեռնում <էջ>');assert(loading.includes('role="status"'));assert(loading.includes('aria-busy="true"'));
assert(loading.includes('Բեռնում &lt;էջ&gt;'));assert.equal((loading.match(/aria-hidden="true"/g)||[]).length,2);
''')


def test_navigation_has_distinct_icons_and_admin_status_is_absent_for_other_roles():
    run_node(SHELL + r'''
for(const role of ['admin','viewer','lecturer','uploader','student']){
 c.S.role=role;c.canUpload=()=>['admin','uploader','lecturer'].includes(role);
 const html=c.shell(role==='student'?'library':'home');
 assert.equal(html.includes('id="worker"'),role==='admin',role);
 assert(!html.includes('Worker'));assert(html.includes('page-loading'));
}
c.S.role='admin';c.canUpload=()=>true;
const nav=c.navigation('stats'),icon=href=>nav.match(new RegExp('<a href="'+href+'"[^>]*>(<svg[\\s\\S]*?</svg>)'))?.[1];
assert(icon('#/'));assert(icon('#/stats'));assert.notEqual(icon('#/'),icon('#/stats'));
assert.notEqual(icon('#/groups'),icon('#/users'));
assert(c.navigation('groups').includes('nav-more has-active'));
assert.equal((nav.match(/aria-current="page"/g)||[]).length,1);
''')


def test_processing_status_never_loads_for_other_roles_or_paints_after_role_change():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),calls=[],c={S:{role:'viewer'},$:()=>({}),set:(s,html)=>calls.push(html)};
let reads=0,resolve;c.loadSummary=()=>{reads++;return Promise.resolve({worker:{alive:true,dry_run:false}})};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(source.slice(source.indexOf('async function updateWorker()'),source.indexOf('async function loadSummary()')),c);
(async()=>{
 for(const role of ['viewer','lecturer','uploader','student']){c.S.role=role;await c.updateWorker()}
 assert.equal(reads,0);assert.equal(calls.length,0);
 c.S.role='admin';await c.updateWorker();assert(calls[0].includes('Մշակում՝ աշխատում է'));assert(!calls[0].includes('Worker'));
 c.loadSummary=async()=>({worker:{alive:false}});await c.updateWorker();assert(calls[1].includes('Մշակում՝ կանգնած'));
 c.loadSummary=()=>new Promise(r=>resolve=r);const old=c.updateWorker();c.S.role='student';resolve({worker:{alive:true}});await old;
 assert.equal(calls.length,2,'late admin response must not paint a different-role shell');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_library_demo_card_has_every_required_field_and_no_playback_action():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const app=fs.readFileSync(process.argv[1],'utf8'),source=fs.readFileSync(process.argv[1].replace('app.js','learning.js'),'utf8');
const c={S:{},ic:()=>'',formatLessonDate:String,fmtDur:String,clock:String};vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(app.slice(app.indexOf('const esc ='),app.indexOf('const STATUS =')),c);vm.runInContext(source,c);
const card=c.libraryCardHtml({job_id:'demo-42',demo:true,playable:false,title:'<Անվանում>',subject:'<Առարկա>',group:'<Խումբ>',lesson_date:'2026-11-20',thumbnail:'/fixture/thumbnail.png',description:'<Նկարագրություն>'});
for(const value of ['/fixture/thumbnail.png','&lt;Անվանում&gt;','&lt;Առարկա&gt;','&lt;Խումբ&gt;','2026-11-20','Ցուցադրական'])assert(card.includes(value),value);
assert(!card.includes('href='));assert(!card.includes('data-notes'));assert(!card.includes('progress'));assert(!card.includes('Դիտել դասը'));
assert(card.includes('&lt;Նկարագրություն&gt;'));assert(card.includes('վիդեո չկա'));
const actual=c.libraryCardHtml({job_id:7,title:'Իրական դաս',subject:'Python',group:'A',url:'https://youtu.be/abcdefghijk',lesson_date:'2026-11-20',chapters:[{start:61,title:'Գլուխ'}]});
assert(actual.includes('href="#/library/7"'));assert(actual.includes('#/library/7?t=61'));assert(actual.includes('data-notes="7"'));
assert(!actual.includes('Ցուցադրական'));assert(!actual.includes('Python |'));
''')


# The existing fake DOM is shared with the player checks; no browser/server is started.



def test_academy_stats_keep_demo_separate_chart_accessible_and_technical_admin_only():
    run_node(SHELL + r"""
c.document={body:{},querySelectorAll:()=>[]};c.window={addEventListener(){}};c.MutationObserver=class{observe(){}};
c.fmtDur=String;c.stat=(icon,tone,label,value,note)=>`<div data-metric="${label}">${value}<small>${note}</small></div>`;
vm.runInContext(fs.readFileSync(process.argv[1].replace('app.js','insights.js'),'utf8'),c);
const data={cards:{published_lessons:3,video_hours:1.5,saved_minutes:35},demo:{lessons:57,video_hours:0,saved_minutes:0},
 by_group:[{group:'<Խումբ>',actual:{published_lessons:3,video_hours:1.5,saved_minutes:35},demo:{lessons:57,video_hours:0}}],
 by_subject:[{group:'<Խումբ>',subject:'<Առարկա>',actual:{published_lessons:3,video_hours:1.5,saved_minutes:35},demo:{lessons:57}}],
 daily:[{date:'2026-11-20',actual_lessons:3,demo_lessons:57}],technical:{avg_processing_sec:42,eta_mean_error_sec:2,eta_samples:1,slow_stages:[]},assumptions:{editing_min_per_hour:30,publishing_min_per_lesson:5}};
for(const role of ['viewer','lecturer']){c.S.role=role;const html=c.academyStatsHtml(data);assert(!html.includes('academy-technical'));assert(!html.includes('Մշակման միջին տևողություն'));}
c.S.role='admin';const html=c.academyStatsHtml(data);
assert(html.includes('data-metric="Հրապարակված դասեր">3<'));assert(!html.includes('data-metric="Հրապարակված դասեր">60<'));
assert(html.includes('57 դաս'));assert(html.includes('չեն ավելացվում իրական հրապարակումների'));assert(html.includes('&lt;Խումբ&gt;'));assert(html.includes('&lt;Առարկա&gt;'));
assert(html.includes('aria-labelledby="academy-chart-title academy-chart-desc"'));assert(html.includes('<th scope="col">Ցուցադրական</th>'));assert(html.includes('academy-chart-demo'));
assert(html.includes('id="academy-technical"'));assert(!html.match(/<details[^>]+academy-technical[^>]*\sopen/));
for(const word of ['ETA','track',' DB '])assert(!html.includes(word));
assert(c.academyStatsChart({daily:[]}).includes('այս ընտրությամբ դասեր չկան'));
""")


def test_academy_stats_filter_zero_options_back_state_polling_and_stale_response():
    run_node(SHELL + DOM + r"""
(async()=>{
 const dom=fakeDOM(),calls=[];Object.assign(c,dom,{window:{addEventListener(){}},URLSearchParams,location:{hash:'#/stats?group=AI+2.6&subject=Math'},
  fmtDur:String,stat:()=>'',api:async(path)=>{calls.push(path);return data}});
 const data={cards:{},demo:{},daily:[],by_group:[],by_subject:[],filters:{groups:[{name:'AI 2.6',count:0,subjects:[{name:'Math',count:0}]},{name:'Empty',count:0,subjects:[{name:'Rust',count:0}]}],subjects:[{name:'Math',count:0}]} };
 vm.runInContext(fs.readFileSync(process.argv[1].replace('app.js','insights.js'),'utf8'),c);
 await c.viewStats();assert.equal(calls[0],'/api/academy/stats?group=AI+2.6&subject=Math');
 assert.equal(dom.ids['#stats-group'].value,'AI 2.6');assert.equal(dom.ids['#stats-subject'].value,'Math');assert(dom.ids['#stats-group'].innerHTML.includes('Empty (0 դաս)'));
 await c.viewStats();assert.equal(calls.length,1,'polling preserves the mounted filter controls');
 dom.ids['#stats-group'].value='Empty';dom.ids['#stats-group'].events.change({target:{id:'stats-group'}});assert.equal(c.location.hash,'#/stats?group=Empty','incompatible subject resets');
 c.location.hash='#/stats?group=AI+2.6&subject=Math';await c.viewStats();assert.equal(dom.ids['#stats-subject'].value,'Math','Back-style hash restores the selection');
 let resolve;c.api=()=>new Promise(r=>resolve=r);c.location.hash='#/stats?group=Empty';const pending=c.viewStats();const body=dom.ids['#academy-stats-body'];
 c.location.hash='#/library';resolve(data);await pending;assert(!body.innerHTML.includes('Ըստ խմբերի'),'late stats cannot replace another route');
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_library_empty_state_clears_filters_but_retains_locked_student_group():
    run_node(SHELL + r"""
const els={'#library-search':{value:'չգտնված'},'#library-group':{value:'OwnGroup',disabled:true},'#library-subject':{value:'Python'},'#library-text-results':{innerHTML:'old'},'#library-grid':{innerHTML:''},'#library-count':{innerHTML:''}};
c.$=s=>els[s];c.set=(s,h)=>els[s].innerHTML=h;c.document={body:{}};c.window={addEventListener(){}};c.MutationObserver=class{observe(){}};c.S.library=[];c.S.libraryText={query:'old'};
vm.runInContext(fs.readFileSync(process.argv[1].replace('app.js','insights.js'),'utf8'),c);
c.renderLibrary();assert(els['#library-grid'].innerHTML.includes('data-library-clear'));assert(els['#library-grid'].innerHTML.includes('Համապատասխան դաս չգտնվեց'));
c.fillLibrarySubjects=()=>{};c.renderLibraryText=()=>{};c.clearLibraryFilters();
assert.equal(els['#library-search'].value,'');assert.equal(els['#library-subject'].value,'');assert.equal(els['#library-group'].value,'OwnGroup');assert.equal(els['#library-text-results'].innerHTML,'');assert.equal(c.S.libraryText,null);
els['#library-group'].disabled=false;c.clearLibraryFilters();assert.equal(els['#library-group'].value,'');
""")
