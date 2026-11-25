"""Upload-page follow-up fixes (the bottom bar never breaks a word is CSS only)."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

from reel import thumbnail as th
from test_web_upload import web, send          # noqa: F401 - fixtures
from test_web_dashboard import isolated               # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node") or "node"


def test_reselected_phone_files_resume_after_reload(isolated):
    """A: iPhone Photos gives a re-selected video a new modification time; after a reload
    the upload must still continue from the bytes the server already has."""
    web, _ = isolated
    c = web.client("uploader")
    body = {"group": "Group-B", "subject": "Python", "lesson_number": 8,
            "files": [{"name": "IMG_0359.MOV", "size": 12, "last_modified": 1}]}
    code, first = c.call("POST", "/api/uploads", body=body)
    assert code == 200
    send(c, first["id"], 0, b"abcd")
    body["files"][0]["last_modified"] = 999
    code, again = c.call("POST", "/api/uploads", body=body)
    assert code == 200 and again["id"] == first["id"] and again["received"] == [4]


def test_progress_ticks_keep_the_cancel_button():
    """B: progress updates (~20/s) must not rebuild the buttons, or a mouse click on
    «Չեղարկել» is lost (mousedown and mouseup land on different elements)."""
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let renders=0;const kids=new Map();
const el=()=>({textContent:'',value:0,onclick:null});
const target={hidden:true,dataset:{},
 set innerHTML(v){renders++;this.html=v;kids.clear();},get innerHTML(){return this.html||'';},
 querySelector(s){if(!this.html||(s==='#up-resume'&&!this.html.includes('id="up-resume"')))return null;if(!kids.has(s))kids.set(s,el());return kids.get(s);},
 querySelectorAll(s){if(!kids.has(s))kids.set(s,(this.html.match(/data-up="part"/g)||[]).map(el));return kids.get(s);}};
const c={console,Promise,Date,Math,JSON,Number,String,encodeURIComponent,S:{role:'uploader'},
 $:s=>s==='#up-progress'?target:null,set:()=>{},toast:()=>{},route:()=>{},esc:String,canUpload:()=>true,
 fmtSize:String,fmtDur:String,byName:()=>0,ic:()=>'',localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},
 navigator:{onLine:true},document:{hidden:false,addEventListener:()=>{}},window:{addEventListener:()=>{}},
 setTimeout:()=>1,clearTimeout:()=>{},confirm:()=>true,api:async()=>({})};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
c.S.up={body:{group:'A',subject:'Python',lesson_number:3,files:[]},id:'x',
 files:[{name:'a.mov',size:100,got:0},{name:'b.mov',size:100,got:0}],
 total:200,state:'sending',message:'',inflight:0,started:Date.now()-1000,base:0};
c.renderUpProgress();const cancel=target.querySelector('#up-cancel');
assert.equal(renders,1);assert.equal(typeof cancel.onclick,'function');assert.equal(target.querySelector('#up-resume'),null);
for(const n of [10,40,90]){c.S.up.inflight=n;c.renderUpProgress();}
c.S.up.files[0].got=50;c.S.up.inflight=40;c.renderUpProgress();
assert.equal(renders,1);assert.equal(target.querySelector('#up-cancel'),cancel);
assert.equal(target.querySelector('progress').value,45);
assert.equal(target.querySelector('[data-up="pct"]').textContent,'45.0%');
assert.deepEqual(target.querySelectorAll('[data-up="part"]').map(e=>e.textContent),['50%','0%']);
c.S.up.state='paused';c.S.up.message='Կապն ընդհատվել է';c.renderUpProgress();
assert.equal(renders,2);assert.equal(typeof target.querySelector('#up-resume').onclick,'function');
"""
    result = subprocess.run([NODE, "-e", script, str(ROOT / "reel/web/static/upload.js")],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_worker_style_assignment_waits_for_the_admins_save(tmp_path, monkeypatch):
    """C: the worker's first assignment for a new group waits while the admin's save holds
    the lock, then reads the saved file, so neither change is lost."""
    monkeypatch.setattr(th, "STYLES_FILE", tmp_path / "thumbnail_styles.json")
    done = threading.Event()
    worker = threading.Thread(target=lambda: (th.group_style("New-Group"), done.set()))
    with th.styles_lock():
        worker.start()
        assert not done.wait(0.3)
        th.STYLES_FILE.write_text(json.dumps({"Group-A": 0}), encoding="utf-8")
    worker.join(5)
    assert done.is_set()
    assert json.loads(th.STYLES_FILE.read_text(encoding="utf-8")) == {"Group-A": 0, "New-Group": 1}


def test_admin_style_save_waits_for_the_worker(isolated):
    """C: the web's style picker takes the same lock as the worker's group_style()."""
    web, _ = isolated
    path = web.root / "thumbnail_styles.json"
    admin = web.client("admin")
    result = {}
    save = threading.Thread(target=lambda: result.update(
        code=admin.call("PUT", "/api/admin/thumbnail-styles/Group-A", body={"style": 3})[0]))
    with th.styles_lock(path):
        save.start()
        save.join(0.3)
        assert save.is_alive()
        path.write_text(json.dumps({"New-Group": 0}), encoding="utf-8")
    save.join(5)
    assert result == {"code": 200}
    assert json.loads(path.read_text(encoding="utf-8")) == {"New-Group": 0, "Group-A": 3}
