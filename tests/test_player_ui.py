"""Fake-YouTube player, watched intervals and account isolation. No server or playback."""
from test_brand import run_node


PRELUDE = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1].replace('app.js','learning.js'),'utf8');
const appSource=fs.readFileSync(process.argv[1],'utf8');
function context(extra={}) {
 const c={S:{role:'student',user:{id:1}},URLSearchParams,performance:{now:()=>0},console,...extra};
 vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
 vm.runInContext(appSource.slice(appSource.indexOf('const esc ='),appSource.indexOf('\n',appSource.indexOf('const esc ='))),c);
 vm.runInContext(source,c);return c;
}
'''


def test_watched_intervals_exclude_seeks_pauses_replays_and_suspended_timers():
    run_node(PRELUDE + r'''
let at=0,pos=0,rate=1;
const player={getCurrentTime:()=>pos,getDuration:()=>200,getPlaybackRate:()=>rate};
const c=context();
const t=c.createWatchTracker({lesson:{job_id:7,duration:200,personal:false},player,valid:()=>true,now:()=>at});
const step=(p,seconds=1)=>{at+=seconds;pos=p;t.sample()};
t.state(1);step(1);step(2);step(3);
assert.equal(t.progress().watched_sec,3);
step(100);assert.equal(t.progress().watched_sec,3,'seeking over 97 seconds earns nothing');
step(101);assert.equal(t.progress().watched_sec,4);
t.state(2);step(130,20);assert.equal(t.progress().watched_sec,4,'paused video earns nothing');
t.seek(1);pos=1;t.state(1);step(2);step(3);assert.equal(t.progress().watched_sec,4,'rewatching is counted once');
rate=2;step(5);assert.equal(t.progress().watched_sec,6,'2× playback earns actual video seconds');
step(25,20);assert.equal(t.progress().watched_sec,6,'suspended browser timer earns nothing');
assert.equal(t.progress().completed,false);
assert.equal(JSON.stringify(c.mergeWatchIntervals([[4,8],[1,5],[8,10],[20,21]])),JSON.stringify([[1,10],[20,21]]));
t.stop(false);
''')


def test_personal_save_body_and_logout_during_save_never_cross_users():
    run_node(PRELUDE + r'''
(async()=>{
 let at=0,pos=0,valid=true,resolve;
 const calls=[],updates=[];
 const c=context({api:(path,options)=>{calls.push({path,body:JSON.parse(options.body)});return new Promise(r=>resolve=r)}});
 const player={getCurrentTime:()=>pos,getDuration:()=>100,getPlaybackRate:()=>1};
 const t=c.createWatchTracker({lesson:{job_id:8,duration:100,personal:true,progress:{percent:22}},player,valid:()=>valid,onProgress:p=>updates.push(p.percent),now:()=>at});
 t.state(1);pos=1;at=1;t.sample();pos=2;at=2;t.sample();
 const pending=t.save();
 assert.equal(calls[0].path,'/api/library/8/progress');
 assert.deepEqual(calls[0].body,{position:2,duration:100,intervals:[[0,2]],expected_user_id:1});
 valid=false;resolve({saved:true,percent:90,completed:true});await pending;
 assert(!updates.includes(90),'late response from the previous identity is ignored');
 pos=50;at=3;t.sample();await t.save();t.stop();assert.equal(calls.length,1,'no request after identity changes');
 assert.equal(c.libraryProgressFor(8),undefined,'personal data never enters legacy memory');
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_resume_transcript_escaping_and_continue_watching_exclude_completed():
    run_node(PRELUDE + r'''
const box={innerHTML:''};const c=context({$:s=>s==='#library-continue'?box:null,clock:String});
assert.equal(c.lessonRoute({job_id:7},61.9),'#/library/7?t=61');
const lesson={job_id:7,duration:200,personal:true,progress:{position:62}};
assert.equal(c.lessonInitialPosition(lesson,'#/library/7'),62);
assert.equal(c.lessonInitialPosition(lesson,'#/library/7?t=0'),0);
assert.equal(c.lessonInitialPosition(lesson,'#/library/7?t=999'),200);
assert.equal(c.lessonInitialPosition(lesson,'#/library/7?t=NaN'),62);
assert.equal(c.activeLessonCue([{start:0,end:2},{start:3,end:5}],3.5),1);
assert.equal(c.activeLessonCue([{start:0,end:2},{start:3,end:5}],2.5),-1);
c.S.library=[{job_id:7,title:'<img>',subject:'A',url:'https://youtu.be/abcdefghijk'},{job_id:8,title:'Completed',subject:'B',url:'https://youtu.be/bbbbbbbbbbb'}];
c.S.libraryProgress={items:[{job_id:7,position:62,percent:30,updated_at:'2026-01-01'},
 {job_id:8,position:90,percent:90,completed:true}]};
c.renderContinueWatching();assert(box.innerHTML.includes('#/library/7'));assert(box.innerHTML.includes('&lt;img&gt;'));
assert(!box.innerHTML.includes('Completed'));
''')


DOM = r'''
function fakeDOM() {
 const ids={},events={},observers=[],timers=new Map();let timer=0;
 class Element {
  constructor(id=''){this.id=id;this.isConnected=true;this.dataset={};this.events={};this.attrs={};this.checked=true;this.value='1';this.offsetTop=0;this.clientHeight=300;this.scrollTop=0;this.nodes={};this._html=''}
  set innerHTML(html){this._html=html;for(const node of Object.values(this.nodes))node.isConnected=false;this.nodes={};
   for(const m of html.matchAll(/id="([^"]+)"/g)){const node=new Element(m[1]);this.nodes['#'+m[1]]=node;ids['#'+m[1]]=node;}
   if(/^<section id=/.test(html)){const first=Object.keys(this.nodes)[0];if(first){this.nodes[first].nodes={...this.nodes};delete this.nodes[first].nodes[first];}}
   if(html.includes('class="learning-preferences-form"'))this.nodes[".learning-preferences-form"]=new Element();
   for(const m of html.matchAll(/<input[^>]*name="([^"]+)"[^>]*>/g)){const node=new Element();node.checked=m[0].includes(" checked");this.nodes['[name="'+m[1]+'"]']=node;}
   for(const m of html.matchAll(/data-cue="(\d+)"/g)){const node=new Element();this.nodes['[data-cue="'+m[1]+'"]']=node;}
  }
  get innerHTML(){return this._html}
  querySelector(s){return this.nodes[s]||Object.values(this.nodes).map(node=>node.querySelector(s)).find(Boolean)||null}
  addEventListener(type,fn){this.events[type]=fn}
  setAttribute(k,v){this.attrs[k]=v}
  removeAttribute(k){delete this.attrs[k]}
  remove(){this.isConnected=false}
 }
 const main=new Element('main');ids['#main']=main;
 return {ids,events,observers,timers,Element,main,
  $:s=>ids[s]?.isConnected?ids[s]:null,
  document:{body:{},head:{append(){}},addEventListener:(type,fn)=>events[type]=fn,getElementById:id=>ids['#'+id],createElement:()=>new Element()},
  MutationObserver:class{constructor(fn){observers.push(fn)}observe(){}},
  setInterval:fn=>{timers.set(++timer,fn);return timer},clearInterval:id=>timers.delete(id),
  setTimeout:fn=>++timer,clearTimeout:()=>{}
 };
}
function playerFixture(extra={}) {
 const dom=fakeDOM(),players=[],calls=[];
 const data={job_id:7,title:'<unsafe>',group:'A',subject:'Python',video_id:'abcdefghijk',url:'https://youtu.be/abcdefghijk',duration:100,personal:true,
 progress:{position:25,percent:10},cues:[{start:0,end:2,text:'<script>bad</script>'},{start:30,end:40,text:'Խոսք'}],chapters:[{start:30,title:'<chapter>'}],summary:null};
 class Player {
  constructor(id,options){this.id=id;this.options=options;this.pos=25;this.rate=1;this.destroyed=false;players.push(this)}
  getCurrentTime(){return this.pos}getDuration(){return 100}getPlaybackRate(){return this.rate}
  getAvailablePlaybackRates(){return [0.5,1,1.5,2]}getIframe(){return {setAttribute(){}}}
  seekTo(p){this.pos=p}playVideo(){this.played=true}setPlaybackRate(rate){this.rate=rate}destroy(){this.destroyed=true}
 }
 const c=context({...dom,window:{YT:{Player},addEventListener(){}},location:{hash:'#/library/7',origin:'https://fixture.test'},ic:()=>'',clock:String,
 api:async(path,options)=>{calls.push(path);return options?{saved:true,percent:10}:data},...extra});
 return {c,dom,players,calls,data};
}
'''


def test_fake_youtube_player_controls_polling_and_dom_cleanup():
    run_node(PRELUDE + DOM + r'''
(async()=>{
 const {c,dom,players,calls}=playerFixture();await c.viewLesson(7);
 assert.equal(players.length,1);const player=players[0];
 assert.equal(player.options.videoId,'abcdefghijk');assert.equal(player.options.playerVars.start,25);
 assert.equal(player.options.playerVars.origin,'https://fixture.test');
 const root=dom.ids['#lesson-view'];assert(root.innerHTML.includes('&lt;script&gt;bad&lt;/script&gt;'));
 assert(root.innerHTML.includes('&lt;chapter&gt;'));assert(root.innerHTML.includes('&lt;unsafe&gt;'));
 player.options.events.onReady({target:player});
 await c.viewLesson(7);assert.equal(players.length,1);assert.equal(calls.length,1,'3-second page poll does not reconstruct or refetch the player');
 root.events.click({target:{closest:()=>({dataset:{lessonSeek:'30'}})}});
 assert.equal(player.pos,30);assert.equal(player.played,true);
 dom.ids['#lesson-speed'].value='1.5';dom.ids['#lesson-speed'].events.change();assert.equal(player.rate,1.5);
 const cue=root.querySelector('[data-cue="1"]');assert.equal(cue.attrs['aria-current'],'true');
 root.isConnected=false;dom.observers[0]();assert.equal(player.destroyed,true);assert.equal(dom.timers.size,0);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_logout_clears_shared_resume_and_does_not_flush_as_next_identity():
    run_node(PRELUDE + DOM + r'''
(async()=>{
 const {c,dom,players,calls,data}=playerFixture();data.personal=false;data.progress=null;c.S.user=null;
 await c.viewLesson(7);const p=players[0];p.options.events.onReady({target:p});
 const t=c.createWatchTracker({lesson:{job_id:9,duration:100,personal:false},player:p,valid:()=>true});t.sample();
 assert(c.libraryProgressFor(9));
 c.S.libraryFacets={groups:[{name:'PrivateAdminGroup'}],watch_stats_available:true};c.S.libraryText={query:'private query'};c.S.library=[{title:'PrivateAdminLesson'}];
 dom.events.click({target:{closest:()=>({id:'logout'})}});
 c.S.user={id:2};c.S.role='student';assert.equal(c.S.libraryFacets,null);assert.equal(c.S.libraryText,null);assert.equal(c.S.library.length,0);
 assert.equal(c.libraryProgressFor(9),undefined);assert.equal(p.destroyed,true);
 assert.equal(calls.filter(path=>path.endsWith('/progress')).length,0);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_late_lesson_fetch_and_youtube_load_cannot_mount_after_navigation():
    run_node(PRELUDE + DOM + r'''
(async()=>{
 let resolve;const {c,dom,players,data}=playerFixture({api:()=>new Promise(r=>resolve=r)});
 const loading=c.viewLesson(7);dom.ids['#lesson-view'].isConnected=false;resolve(data);await loading;
 assert.equal(players.length,0);
 const apiFixture=playerFixture();delete apiFixture.c.window.YT;
 const loadingAPI=apiFixture.c.viewLesson(7);for(let i=0;i<12 && !apiFixture.c.window.onYouTubeIframeAPIReady;i++) await Promise.resolve();
 apiFixture.dom.ids['#lesson-view'].isConnected=false;
 apiFixture.c.window.YT={Player:function(){throw Error('must not mount stale iframe')}};
 assert.equal(typeof apiFixture.c.window.onYouTubeIframeAPIReady,'function',apiFixture.dom.ids['#lesson-view'].innerHTML);
 apiFixture.c.window.onYouTubeIframeAPIReady();await loadingAPI;
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_demo_metadata_card_has_no_player_notes_or_progress_actions():
    run_node(PRELUDE + r'''
const c=context({ic:()=>'',fmtDur:String,formatLessonDate:String,clock:String});
const demo={job_id:'demo-row-7',demo:true,playable:false,title:'<demo>',description:'<description>',group:'Group-A',subject:'Python',thumbnail:null,url:null,chapters:[]};
const html=c.libraryCardHtml(demo);
assert(html.includes('Ցուցադրական դաս · վիդեո չկա'));assert(html.includes('&lt;demo&gt;'));assert(html.includes('&lt;description&gt;'));
assert(!html.includes('href='));assert(!html.includes('data-notes'));assert(!html.includes('Գլուխներ'));assert(!html.includes('learning-progress'));
assert.equal(c.isPlayableLesson(demo),false);
assert.equal(c.isPlayableLesson({job_id:7,url:'https://youtu.be/abcdefghijk'}),true);
''')



def test_watch_statistics_escape_every_name_and_present_all_three_views():
    run_node(PRELUDE + r"""
const c=context({ic:()=>'',fmtDur:String});
const html=c.watchStatisticsHtml({threshold:90,lessons:[{job_id:7,title:'<lesson>',group:'<group>',subject:'Python',published_at:'2026-01-01',students:2,started:1,completed:0,average_percent:22.5}],
 students:[{id:1,name:'<student>',group:'<group>',percent:22.5,completed:0,total_lessons:1,watched_sec:22.5,lessons:[{job_id:7,percent:22.5,completed:false}]}],
 missed:[{name:'<missed>',group:'<group>',lessons:[{job_id:7,title:'<lesson>'}]}]});
for(const text of ['&lt;lesson&gt;','&lt;student&gt;','&lt;missed&gt;','Ըստ դասերի','Ըստ ուսանողների','Վերջին երկու դասերը չդիտած','90%','Առաջ տալը'])assert(html.includes(text),text);
assert(!html.includes('<student>'));assert(!html.includes('<lesson>'));assert(html.includes('#/library/7'));assert(!html.includes('mailto:'));
const box={innerHTML:''};c.$=()=>box;c.S.user=null;c.S.libraryFacets={watch_stats_available:true};c.renderLearningLinks();assert(box.innerHTML.includes('#/library/watch-stats'));
c.S.libraryFacets.watch_stats_available=false;c.renderLearningLinks();assert.equal(box.innerHTML,'');
""")


def test_watch_stats_preserve_filter_hash_throttle_polling_and_ignore_stale_response():
    run_node(PRELUDE + DOM + r"""
(async()=>{
 const dom=fakeDOM(),calls=[],data={groups:[{name:'Group A'},{name:'B'}],selected_group:'Group A',lessons:[],students:[],missed:[]};
 const c=context({...dom,window:{addEventListener(){}},location:{hash:'#/library/watch-stats?group=Group%20A'},ic:()=>'',fmtDur:String,
 api:async path=>{calls.push(path);return data}});
 await c.viewWatchStats();assert.equal(calls[0],'/api/library/watch-stats?group=Group%20A');
 assert.equal(dom.ids['#watch-group'].value,'Group A');
 await c.viewWatchStats();assert.equal(calls.length,1,'page polling is throttled and preserves the filter');
 dom.ids['#watch-group'].events.change({target:{value:'B & C'}});assert.equal(c.location.hash,'#/library/watch-stats?group=B+%26+C');
 let resolve;c.api=()=>new Promise(r=>resolve=r);const pending=c.viewWatchStats();
 const oldBody=dom.ids['#watch-content'];c.location.hash='#/library';resolve(data);await pending;
 assert(!oldBody.innerHTML.includes('Ըստ դասերի'),'stale response cannot paint after navigation');
})().catch(e=>{console.error(e);process.exitCode=1});
""")



def test_notification_preferences_defaults_save_only_own_booleans_and_survive_polling():
    run_node(PRELUDE + DOM + r"""
(async()=>{
 const dom=fakeDOM(),target=new dom.Element(),calls=[];
 const c=context({...dom,window:{addEventListener(){}},ic:()=>'',api:async(path,opts)=>{calls.push({path,opts});return opts?{personal:true,user_id:1,...JSON.parse(opts.body)}:{personal:true,user_id:1,new_lessons:true,weekly_digest:true}}});
 await c.mountLearningPreferences(target);assert(target.innerHTML.includes('novalidate'));
 assert(target.innerHTML.includes('Նոր դասերի ծանուցումներ'));assert(target.innerHTML.includes('Շաբաթական դասերի ամփոփագիր'));
 const news=target.querySelector('[name="new_lessons"]'),weekly=target.querySelector('[name="weekly_digest"]'),form=target.querySelector('.learning-preferences-form');
 assert(news.checked&&weekly.checked);news.checked=false;
 await c.mountLearningPreferences(target);assert.equal(calls.length,1,'page poll does not reload or overwrite changed checkboxes');assert.equal(news.checked,false);
 await form.events.submit({preventDefault(){}});
 assert.equal(calls[1].path,'/api/library/notifications');assert.equal(calls[1].opts.method,'PUT');
 assert.deepEqual(JSON.parse(calls[1].opts.body),{new_lessons:false,weekly_digest:true,expected_user_id:1});assert(!Object.keys(JSON.parse(calls[1].opts.body)).includes('user_id'));
 assert(target.querySelector('#learning-mail-status').textContent.includes('պահպանված'));assert.equal(news.disabled,false);
 c.S.user={id:2};await form.events.submit({preventDefault(){}});assert.equal(calls.length,2,'old account form cannot submit after identity change');
 const box={innerHTML:''};c.$=()=>box;c.S.libraryFacets={};c.renderLearningLinks();assert(box.innerHTML.includes('#/library/settings'));
 c.S.user=null;c.renderLearningLinks();assert.equal(box.innerHTML,'','shared sessions have no personal settings link');
})().catch(e=>{console.error(e);process.exitCode=1});
""")


def test_notification_shared_account_and_stale_get_never_offer_personal_controls():
    run_node(PRELUDE + DOM + r"""
(async()=>{
 const dom=fakeDOM(),target=new dom.Element(),calls=[];
 const c=context({...dom,window:{addEventListener(){}},api:async(path,opts)=>{calls.push(path);return {personal:false,new_lessons:false,weekly_digest:false}}});
 c.S.user=null;await c.mountLearningPreferences(target);
 assert(!target.innerHTML.includes('<form'));assert(!target.innerHTML.includes('checkbox'));assert(target.innerHTML.includes('Ընդհանուր մուտքով'));
 await c.mountLearningPreferences(target);assert.equal(calls.length,1);
 let resolve;c.S.user={id:1};c.api=()=>new Promise(r=>resolve=r);const detached=new dom.Element();const pending=c.mountLearningPreferences(detached);
 detached.isConnected=false;resolve({personal:true,user_id:1,new_lessons:true,weekly_digest:true});await pending;
 assert(!detached.innerHTML.includes('checkbox'),'late settings response cannot paint after navigation');
})().catch(e=>{console.error(e);process.exitCode=1});
""")



def test_cross_tab_cookie_change_is_guarded_on_progress_and_preferences():
    run_node(PRELUDE + DOM + r"""
(async()=>{
 let pos=0,at=0;const calls=[];
 const c=context({api:async(path,opts)=>{calls.push(JSON.parse(opts.body));c.S.role=null;throw Error('auth')}});
 const player={getCurrentTime:()=>pos,getDuration:()=>100,getPlaybackRate:()=>1};
 const tracker=c.createWatchTracker({lesson:{job_id:7,personal:true,duration:100},player,valid:()=>!!c.S.role,now:()=>at});
 tracker.state(1);pos=1;at=1;tracker.sample();await tracker.save();assert.equal(calls[0].expected_user_id,1);
 pos=2;at=2;tracker.sample();await tracker.save();assert.equal(calls.length,1,'identity-guard401 prevents any later progress writes');
 const dom=fakeDOM(),target=new dom.Element();let login=0;
 const prefs=context({...dom,window:{addEventListener(){}},renderLogin:()=>login++,api:async()=>({personal:true,user_id:2,new_lessons:true,weekly_digest:true})});
 await prefs.mountLearningPreferences(target);assert.equal(prefs.S.role,null);assert.equal(login,1);
 assert(!target.innerHTML.includes('checkbox'),'user B preferences never mount under stale user A identity');
})().catch(e=>{console.error(e);process.exitCode=1});
""")
