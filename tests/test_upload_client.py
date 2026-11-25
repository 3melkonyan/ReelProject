"""Run the upload controller as a JS unit (fake XHR/network, no browser automation)."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize("wake_mode", ["success", "rejected", "sync_error", "absent"])
def test_phone_upload_recovers_partial_chunk_and_lost_finish_response(wake_mode):
    root=Path(__file__).resolve().parents[1]
    node=shutil.which("node") or "node"
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const events={},docEvents={},requests=[],stored=new Map(),wakeMode=process.argv[2];
let received=0,receipt=false,failChunk=true,finishes=0,wakes=0,releases=0,timer;
const context={console,Promise,Date,Math,JSON,Number,String,encodeURIComponent,
 S:{role:'uploader'},$:()=>null,set:()=>{},toast:()=>{},route:()=>{},esc:String,
 canUpload:()=>true,fmtSize:String,fmtDur:String,byName:()=>0,ic:()=>'',
 localStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
 navigator:{onLine:true,wakeLock:{request:()=>{wakes++;if(wakeMode==='sync_error')throw new Error('unsupported');if(wakeMode==='rejected')return Promise.reject(new Error('denied'));return Promise.resolve( {addEventListener:()=>{},release:async()=>{releases++;}});}}},
 document:{hidden:false,addEventListener:(k,fn)=>docEvents[k]=fn},
 window:{addEventListener:(k,fn)=>events[k]=fn},
 setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{},confirm:()=>true,
 api:async(path,opts={})=>{
   if(path==='/api/uploads')return {id:'abc',chunk:8};
   if(path.endsWith('/finish')){finishes++;receipt=true;const e=new Error('lost acknowledgement');e.net=true;throw e;}
   return receipt?{completed:true,result:{job_id:42}}:{completed:false,received:[received],chunk:8};
 }
};
if(wakeMode==='absent')delete context.navigator.wakeLock;
context.XMLHttpRequest=class {
 constructor(){this.upload={};this.status=200;}
 open(method,url){this.url=url;requests.push(url);}
 setRequestHeader(){}
 send(blob){queueMicrotask(()=>{
   this.upload.onprogress({loaded:blob.size});
   if(failChunk){failChunk=false;received=3;this.onerror();return;}
   const offset=Number(this.url.split('offset=')[1]);assert.equal(offset,received);
   received+=blob.size;this.responseText=JSON.stringify({received});this.onload();
 });}
};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
 context.S.upFiles=[{slice:(a,b)=>({size:b-a})}];
 context.S.up={body:{group:'A',subject:'Python',lesson_number:3},id:null,files:[{name:'one.mov',size:20,got:0}],total:20,state:'paused',started:Date.now()};
 let prevented=false;events.beforeunload({preventDefault:()=>prevented=true});assert.ok(prevented);
 await context.resumeUpload();assert.equal(context.S.up.state,'paused');assert.equal(received,3);assert.ok(timer);
 // online and visibility can arrive together: only one worker may send chunks.
 await Promise.all([events.online(),docEvents.visibilitychange()]);
 // visibility handler does not return its promise; let the active worker settle.
 for(let i=0;i<30;i++)await Promise.resolve();
 assert.equal(received,20);assert.equal(context.S.up.state,'paused');assert.equal(finishes,1);
 const puts=requests.length;await context.resumeUpload();
 assert.equal(context.S.up,null);assert.equal(requests.length,puts);assert.equal(finishes,1);
 assert.deepEqual(requests.map(s=>Number(s.split('offset=')[1])),[0,3,11,19]);
 if(wakeMode==='success')assert.ok(wakes>=2 && releases>=2);else assert.equal(releases,0);assert.equal(stored.size,0);
 prevented=false;events.beforeunload({preventDefault:()=>prevented=true});assert.equal(prevented,false);
 console.log('partial chunk + online/visibility + lost finish + wake lock: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result=subprocess.run([node,"-e",script,str(root/"reel/web/static/upload.js"),wake_mode],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_known_auto_notes_are_armenian_and_unknown_text_is_escaped():
    root=Path(__file__).resolve().parents[1]
    node=shutil.which("node") or "node"
    script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('function autoDecisionLine(info)'),end=source.indexOf('async function viewJob(',start);
const c={esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};vm.createContext(c);vm.runInContext(source.slice(start,end),c);
const known=['unclear windows','plan cut','planned result too short','grounded chapters unavailable','LLM metadata unavailable','LLM chapter wording unusable','3 transcript segments have broken timestamps','loudness -17 LUFS','the group\'s lecturer photos match','lecturer choice file unreadable','lecturer choice file invalid','the lecturer chosen on the job page','lecturer unclear','only 2 clear photos','the recognised lecturer matches','thumbnail skipped','thumbnail not set after'];
for(const note of known){const html=c.autoDecisionLine({auto_note:note+' <img src=x onerror=alert(1)>'});assert.match(html,/Ավտոմատ որոշում/);assert.ok(!html.includes(note));assert.ok(!html.includes('<img'));}
const unknown=c.autoDecisionLine({auto_note:'future <img src=x onerror=alert(1)>'});assert.ok(unknown.includes('&lt;img'));assert.ok(!unknown.includes('<img'));
"""
    result=subprocess.run([node,"-e",script,str(root/"reel/web/static/app.js")],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
