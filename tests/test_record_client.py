"""G-40/4 item 1, browser side: record.js and the upload order run as JS units in Node.

Fake MediaRecorder, fetch, IndexedDB-less storage (memory fallback), wake lock and network; no browser.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "reel" / "web" / "static"
NODE = shutil.which("node") or "node"
pytestmark = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not available")


def run(script, *files):
    result = subprocess.run([NODE, "-e", script, *map(str, files)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


FAKE = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const docEvents={},winEvents={},stored=new Map(),calls=[],server={parts:[],received:{},closed:{},deleted:new Set(),confirmed:false};
let online=true,wakes=0,releases=0,recorders=[];
const el=()=>({hidden:false,textContent:'',value:'',dataset:{},innerHTML:'',disabled:false,play:async()=>{},srcObject:null});
const nodes={};
const absent=new Set(['#rec-https','#rec-view']);   // page containers that do not exist yet
const $=s=>absent.has(s)?null:(nodes[s]||(nodes[s]=el()));
class MediaRecorder{
  static isTypeSupported(t){return t.startsWith('video/mp4');}
  constructor(stream,opts){this.stream=stream;this.opts=opts;this.state='inactive';recorders.push(this);}
  start(slice){this.state='recording';this.slice=slice;}
  stop(){this.state='inactive';setTimeout(()=>{this.ondataavailable({data:blob('TAIL')});this.onstop();},0);}
  requestData(){}
  emit(text){this.ondataavailable({data:blob(text)});}
}
function blob(text){const bytes=Buffer.from(text);return {size:bytes.length,slice:(a,b)=>blob(bytes.subarray(a,b).toString()),arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.length)};}
const track={readyState:'live',addEventListener:()=>{},stop(){this.readyState='ended';}};
const stream={getVideoTracks:()=>[track],getTracks:()=>[track]};
async function fetch(path,opts){
  calls.push(opts.method+' '+path);
  if(!online)throw new Error('offline');
  const json=(status,data)=>({status,json:async()=>data});
  const body=opts.body&&typeof opts.body==='string'?JSON.parse(opts.body):null;
  if(opts.method==='POST'&&path==='/api/recordings')return json(200,{id:'abcdefabcdefabcdefab',lesson_date:'2026-11-20',parts:[]});
  let m;
  if(opts.method==='POST'&&path.endsWith('/parts')){server.parts.push(server.parts.length);return json(200,{index:server.parts.length-1});}
  if((m=path.match(/^\/api\/uploads\/\w+\/(\d+)\?offset=(\d+)$/))){
    const i=+m[1],off=+m[2],have=server.received[i]||'';
    if(server.deleted.has(i))return json(410,{detail:'part deleted'});
    if(off!==have.length)return json(409,{received:have.length});
    server.received[i]=have+Buffer.from(opts.body).toString();return json(200,{received:server.received[i].length});
  }
  if((m=path.match(/\/parts\/(\d+)\/close$/))){
    const i=+m[1],have=(server.received[i]||'').length;
    if(body.size!==have)return json(409,{received:have});
    server.closed[i]=body;return json(200,{ok:true,size:body.size});
  }
  if(opts.method==='DELETE'&&(m=path.match(/\/parts\/(\d+)$/))){server.deleted.add(+m[1]);return json(200,{ok:true});}
  if(opts.method==='GET'&&path==='/api/recordings/abcdefabcdefabcdefab')
    return json(200,{completed:false,parts:server.parts.filter(i=>!server.deleted.has(i)).map(i=>({index:i,received:(server.received[i]||'').length}))});
  if(opts.method==='POST'&&path.endsWith('/confirm')){server.confirmed=true;return json(200,{job_id:7});}
  return json(404,{detail:'unexpected '+path});
}
const context={console,Promise,Date,Math,JSON,Number,String,Boolean,Array,Set,Map,Buffer,Object,
  S:{role:'lecturer',last:{}},$,set:(s,h)=>{$(s).innerHTML=h;},esc:String,ic:()=>'',clock:s=>String(Math.round(s)),
  fmtSize:String,formatLessonDate:String,toast:()=>{},canUpload:()=>true,api:async()=>({groups:[]}),
  localStorage:{getItem:k=>stored.has(k)?stored.get(k):null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
  navigator:{onLine:true,mediaDevices:{getUserMedia:async()=>stream},wakeLock:{request:async()=>{wakes++;return {addEventListener:()=>{},release:async()=>{releases++;}};}}},
  document:{hidden:false,addEventListener:(k,fn)=>docEvents[k]=fn},
  window:{isSecureContext:true,addEventListener:(k,fn)=>winEvents[k]=fn},location:{hash:'#/record',origin:'https://reel.example'},
  setTimeout,clearTimeout,setInterval:()=>1,clearInterval:()=>{},confirm:()=>true,fetch,MediaRecorder,
  indexedDB:undefined,IDBKeyRange:undefined,AbortController,
};
context.window.isSecureContext=true;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const R=vm.runInContext('R',context);
const settle=async(n=25)=>{for(let i=0;i<n;i++)await new Promise(r=>setTimeout(r,1));};
"""


def test_record_parts_stream_survive_offline_interruption_delete_and_confirm():
    run(FAKE + r"""
(async()=>{
  $('#rec-group').value='AI 2.6';$('#rec-subject').value='Python';$('#rec-number').value='5';
  await context.recStartPart();
  assert.equal(R.s.mime,'video/mp4;codecs=avc1.640028,mp4a.40.2');assert.equal(recorders[0].slice,2000);
  assert.equal(recorders[0].opts.videoBitsPerSecond,4000000);
  recorders[0].emit('part1-a|');await settle();
  assert.equal(server.received[0],'part1-a|');assert.ok(wakes>=1,'wake lock while recording');
  online=false;recorders[0].emit('part1-b|');await settle();                // network drops: chunk waits locally
  assert.equal(server.received[0],'part1-a|');assert.equal(R.offline,true);
  docEvents.visibilitychange&&(context.document.hidden=true,docEvents.visibilitychange());   // screen locked
  await settle();
  assert.equal(R.rec,null);assert.equal(R.s.parts[0].interrupted,true);assert.equal(R.s.parts[0].state,'closing');
  context.document.hidden=false;online=true;await context.recPump();await settle();
  assert.equal(server.received[0],'part1-a|part1-b|TAIL');
  assert.deepEqual(server.closed[0].size,'part1-a|part1-b|TAIL'.length);assert.equal(R.s.parts[0].state,'closed');
  await context.recStartPart();recorders[1].emit('offtopic|');await settle();
  context.recStop(false);await settle();
  assert.equal(R.s.parts[1].state,'closed');
  await context.recDelete(R.s.parts[1].n);await settle();
  assert.ok(server.deleted.has(1));assert.equal(R.s.parts[1].state,'deleted');
  await context.recStartPart();recorders[2].emit('part3|');context.recStop(false);await settle();
  assert.equal(server.received[2],'part3|TAIL');assert.equal(context.recReady(),true);
  await context.recConfirm();await settle();
  assert.equal(server.confirmed,true);assert.deepEqual(R.done,{job_id:7});assert.equal(R.s,null);
  assert.equal(stored.has('reel-record-v1'),false,'local session cleared after confirm');
  assert.ok(releases>=1,'wake lock released');
  console.log('record client: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
""", STATIC / "record.js")


def test_reload_closes_the_part_that_was_recording_and_sends_the_rest():
    run(FAKE + r"""
(async()=>{
  server.parts=[0];server.received[0]='head|';
  stored.set('reel-record-v1',JSON.stringify({local:'Lx',id:'abcdefabcdefabcdefab',group:'G',subject:'S',lesson_number:2,mime:'video/mp4',
    parts:[{n:1,index:0,bytes:5,sent:5,state:'recording',started:1000,last:61000,duration:0}]}));
  R.mem.set('Lx:001:0000000000005',{key:'Lx:001:0000000000005',local:'Lx',n:1,offset:5,data:new Uint8Array(Buffer.from('rest|')).buffer});
  await context.recRecover();await settle();
  assert.equal(server.received[0],'head|rest|');assert.equal(server.closed[0].size,10);
  assert.equal(server.closed[0].duration,60);assert.equal(server.closed[0].interrupted,true);
  assert.equal(R.s.parts[0].state,'closed');assert.equal(context.recReady(),true);
  console.log('recover: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
""", STATIC / "record.js")


def test_insecure_page_explains_https_and_offers_the_public_address():
    run(FAKE + r"""
(async()=>{
  context.window.isSecureContext=false;context.api=async p=>{assert.equal(p,'/api/recordings/info');return {public_url:'https://reel.example.org'};};
  await context.viewRecord();await settle();
  const html=$('#main').innerHTML;
  assert.ok(html.includes('https'));assert.ok(html.includes('https://reel.example'));assert.ok(html.includes('#/upload'));
  assert.equal($('#rec-https-link').href,'https://reel.example.org/#/record');assert.equal($('#rec-https-link').hidden,false);
  console.log('https: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
""", STATIC / "record.js")


def test_upload_orders_parts_by_recording_time_and_sends_the_chosen_order():
    run(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
function mp4(created){  // ftyp + moov(mvhd v0) with a creation time in QuickTime seconds
  const ftyp=Buffer.alloc(16);ftyp.writeUInt32BE(16,0);ftyp.write('ftypisom',4);
  const mvhd=Buffer.alloc(108);mvhd.writeUInt32BE(108,0);mvhd.write('mvhd',4);mvhd.writeUInt32BE(created/1000+2082844800,12);
  const moov=Buffer.alloc(8);moov.writeUInt32BE(8+108,0);moov.write('moov',4);
  const bytes=Buffer.concat([ftyp,moov,mvhd]);
  const file=(b)=>({size:b.length,slice:(a,c)=>file(b.subarray(a,c)),arrayBuffer:async()=>b.buffer.slice(b.byteOffset,b.byteOffset+b.length)});
  return file(bytes);
}
const list={innerHTML:''},fields={'#up-files-list':list};
const c={S:{role:'uploader'},$:s=>fields[s]||null,esc:String,fmtSize:String,byName:(a,b)=>a.name.localeCompare(b.name),Date,Promise,Math,Number,String,JSON,
  localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},document:{addEventListener:(k,fn)=>{if(k==='click')c.click=fn;}},window:{addEventListener:()=>{}},console};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
(async()=>{
  const at=Date.UTC(2026,10,20,9,0,0);
  const later=Object.assign(mp4(at+3600e3),{name:'IMG_0001.MOV',lastModified:1}),earlier=Object.assign(mp4(at),{name:'IMG_0002.MOV',lastModified:2});
  assert.equal(await c.videoCreated(earlier),at);
  await c.pickUploadFiles([later,earlier]);
  assert.deepEqual(c.S.upOrder.map(i=>i.file.name),['IMG_0002.MOV','IMG_0001.MOV']);
  assert.ok(list.innerHTML.includes('նկարահանվել է'));
  c.click({target:{closest:()=>({dataset:{upMove:'1',dir:'-1'}})}});
  assert.deepEqual(c.S.upOrder.map(i=>i.file.name),['IMG_0001.MOV','IMG_0002.MOV']);
  Object.assign(fields,{'#up-files':{files:[later,earlier]},'#up-group':{value:'A'},'#up-subject':{value:'Python'},'#up-number':{value:'4'},'#up-date':{value:'2026-11-20',dataset:{changed:'false'}}});
  vm.runInContext('resumeUpload=async()=>{}',c);await c.startUpload();
  assert.equal(c.S.up.body.ordered,true);assert.deepEqual(c.S.up.body.files.map(f=>f.name),['IMG_0001.MOV','IMG_0002.MOV']);
  assert.equal(await c.videoCreated({size:4,slice:()=>({arrayBuffer:async()=>new ArrayBuffer(4)})}),null);
  console.log('upload order: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
""", STATIC / "upload.js")


def test_record_page_is_routed_for_uploaders_and_assets_are_loaded():
    app = (STATIC / "app.js").read_text()
    assert '["record","mic","Ձայնագրել"]' in app and "fn = viewRecord" in app
    index = (STATIC / "index.html").read_text()
    assert '<script src="/record.js"></script>' in index and '/g40.css' in index
    text = (STATIC / "record.js").read_text()
    assert not any(p in text for p in ("Քո ", "քո ", "կարող ես", "Ընտրիր", "Ստուգիր", "Փորձիր"))


def test_unavailable_indexeddb_warns_and_bounds_memory_recording():
    run(FAKE + r"""
(async()=>{
  let stopped=0;context.recStop=()=>{stopped++;R.rec=null;};R.rec={};
  await context.recPut({key:'large',local:'demo',data:{byteLength:64*1024*1024}});
  await settle();
  assert.equal(stopped,1);assert.ok(R.notice.includes('Մի փակեք էջը'));
  assert.equal(R.mem.size,1,'the final buffered chunk is retained until upload');
  console.log('bounded memory fallback: ok');
})().catch(e=>{console.error(e);process.exitCode=1;});
""", STATIC / "record.js")
