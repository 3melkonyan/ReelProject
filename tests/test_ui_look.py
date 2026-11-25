"""Selected assets, keyboard menu, role navigation and upload date intent."""
from pathlib import Path
import hashlib,json,subprocess,xml.etree.ElementTree as ET
from PIL import Image
from test_brand import run_node,STATIC,NODE


def test_selected_signal_icons_and_no_third_party_logos():
    brand=STATIC/'brand'
    assert not any('academy' in p.name for p in brand.iterdir())
    for name,size in [('icon-1024',1024),('icon-512',512),('icon-192',192),('apple-touch-icon',180),('favicon',32),('icon-16',16)]:
        png=Image.open(brand/f'{name}.png').convert('RGBA');assert png.size==(size,size)
        assert png.getpixel((0,0))[3]==0 and max(png.getpixel((size//2,0))[:3])<=8
        root=ET.parse(brand/f'{name}.svg').getroot();assert root.get('viewBox')=='0 0 1024 1024'
        assert not any(x.tag.endswith(('image','text')) for x in root.iter())
    assert Image.open(brand/'favicon.ico').ico.sizes()=={(16,16),(32,32)}
    css=(STATIC/'app.css').read_text()
    assert 'academy-wordmark' not in css and 'academy-mark' not in css
    manifest=json.loads((STATIC/'manifest.webmanifest').read_text())
    assert manifest['short_name']=='Reel' and manifest['background_color']=='#000000'


def test_keyboard_menu_enter_escape_arrow_focus_and_aria():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const handlers={},attrs={'aria-expanded':'false'},elements={};
const document={querySelector:s=>elements[s],addEventListener:(k,fn)=>(handlers[k]??=[]).push(fn),activeElement:null};
const item=()=>({focus(){document.activeElement=this},closest:()=>({})});
const items=[item(),item(),item()];
const toggle={id:'account-toggle',dataset:{},setAttribute:(k,v)=>attrs[k]=v,getAttribute:k=>attrs[k],focus(){document.activeElement=this}};
const menu={hidden:true,querySelector:()=>items[0],querySelectorAll:()=>items,contains:x=>items.includes(x)};
Object.assign(elements,{'#app':{},'#account-toggle':toggle,'#account-popup':menu});
const c={document,window:{addEventListener:()=>{}},location:{hash:'#/upload'},console};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
let prevented=0;const key=(name,target)=>handlers.keydown.forEach(fn=>fn({key:name,target,preventDefault:()=>prevented++}));
key('Enter',toggle);assert.equal(attrs['aria-expanded'],'true');assert.equal(menu.hidden,false);assert.equal(document.activeElement,items[0]);
key('ArrowDown',items[0]);assert.equal(document.activeElement,items[1]);
key('End',items[1]);assert.equal(document.activeElement,items[2]);
key('Escape',items[2]);assert.equal(menu.hidden,true);assert.equal(attrs['aria-expanded'],'false');assert.equal(document.activeElement,toggle);assert.equal(prevented,4);
''')


def test_lecturer_navigation_and_profile_menu_do_not_expose_admin_pages():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'lecturer',user:{name:'Teacher'}},esc:String,ic:()=>'',isDark:()=>true,canUpload:()=>true};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(source.slice(source.indexOf('const navLink ='),source.indexOf('function route()')),c);
const nav=c.navigation('library');assert(nav.includes('href="#/library"')&&nav.includes('href="#/stats"'));
assert(!nav.includes('href="#/users"')&&!nav.includes('href="#/settings"'));
const shell=c.shell('library');assert(shell.includes('href="#/profile"')&&shell.includes('href="#/about"'));
assert.equal((shell.match(/id="logout"/g)||[]).length,1);assert(shell.includes('aria-haspopup="menu"'));
''')


def test_upload_preserves_explicit_date_intent_in_resumable_body():
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const fields={'#up-files':{files:[{name:'lesson.mov',size:10}]},'#up-group':{value:'A'},'#up-subject':{value:'Python'},'#up-number':{value:'7'},'#up-date':{value:'2024-01-02',dataset:{changed:'true'}}};
const c={S:{role:'uploader'},$:s=>fields[s],localStorage:{getItem:()=>null,setItem:()=>{}},document:{addEventListener:()=>{}},window:{addEventListener:()=>{}},byName:()=>0,toast:()=>{},console};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);vm.runInContext('resumeUpload=async()=>{}',c);
(async()=>{await c.startUpload();assert.equal(c.S.up.body.lesson_date,'2024-01-02');assert.equal(c.S.up.body.lesson_date_changed,true);assert.equal(c.S.up.body.lesson_number,7);})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result=subprocess.run([NODE,'-e',script,str(STATIC/'upload.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_formal_address_in_changed_frontend_files():
    for name in ('app.js','upload.js','insights.js'):
        text=(STATIC/name).read_text()
        assert not any(phrase in text for phrase in ('Քո ','քո ','կարող ես','Ընտրիր','ընտրիր','Ստուգիր','Փորձիր','Հաստատո՞ւմ ես'))


def test_date_editor_keeps_unsaved_input_across_job_polls():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let renders=0,request;
const button={disabled:false},input={value:'2024-01-01'},message={},form={querySelector:()=>button};
const target={dataset:{},set innerHTML(v){renders++;this.html=v},get innerHTML(){return this.html}};
const elements={'#app':{},'#jd-date':target,'#lesson-date-form':form,'#lesson-date':input,'#lesson-date-message':message};
const c={document:{querySelector:s=>elements[s],addEventListener:()=>{}},window:{addEventListener:()=>{}},console};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
vm.runInContext('S.role="admin";api=async(url,options)=>{globalThis.sent=JSON.parse(options.body);return {lesson_date:sent.lesson_date}}',c);
const job={id:5,lesson_date:'2024-01-01'};c.renderLessonDate(job);input.value='2024-02-03';
c.renderLessonDate(job);assert.equal(renders,1);assert.equal(input.value,'2024-02-03');
(async()=>{await form.onsubmit({preventDefault:()=>{},target:form});assert.equal(c.sent.lesson_date,'2024-02-03');assert.equal(button.disabled,false);assert(message.textContent.includes('պահպանված'));})().catch(e=>{console.error(e);process.exitCode=1});
''')
