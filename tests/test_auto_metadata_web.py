"""Chapterless metadata and persisted fallback notes on the job page."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_web import web  # noqa: F401 - source-exec app with temporary DB/work/secrets


def write_metadata(web, note="grounded chapters unavailable — default metadata"):
    value = {"title": "Դաս՝ Փորձնական, 2026-11-19", "description": "Դասի տեսագրություն։",
             "youtube_description": "Դասի տեսագրություն։", "chapters": [], "auto_note": note}
    (web.work / "metadata.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def stage(web, status="done", note=None):
    def update(database):
        database.stage_start(web.job, "metadata")
        output = {"title": "Դաս", "description": "Նկարագրություն", "chapters": []}
        if note is not None:
            output["auto_note"] = note
        if status == "done":
            database.stage_done(web.job, "metadata", output)
        elif status == "failed":
            database.stage_failed(web.job, "metadata", "critical source failure")
    web.mutate(update)


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_done_metadata_note_is_visible_even_with_an_older_wrapper_output(web, role):
    metadata = write_metadata(web)
    stage(web)
    reply = web.client(role).request("GET", f"/api/jobs/{web.job}")
    assert reply.status == 200
    body = reply.json()
    info = next(item["info"] for item in body["stages"] if item["name"] == "metadata")
    assert info["auto_note"] == metadata["auto_note"]
    assert body["metadata"]["chapters"] == []
    assert body["metadata"]["title"] == metadata["title"]


def test_explicit_database_note_takes_precedence_over_older_artifact(web):
    write_metadata(web, "older artifact note")
    stage(web, note="current stage note")
    body = web.client("viewer").request("GET", f"/api/jobs/{web.job}").json()
    info = next(item["info"] for item in body["stages"] if item["name"] == "metadata")
    assert info["auto_note"] == "current stage note"


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_unfinished_metadata_does_not_show_a_stale_artifact_note(web, status):
    write_metadata(web)
    if status != "pending":
        stage(web, status)
    body = web.client("viewer").request("GET", f"/api/jobs/{web.job}").json()
    info = next(item["info"] for item in body["stages"] if item["name"] == "metadata")
    assert "auto_note" not in info


def test_nontext_artifact_notes_are_not_exposed(web):
    write_metadata(web, {"private": "not a human-readable decision"})
    stage(web)
    body = web.client("viewer").request("GET", f"/api/jobs/{web.job}").json()
    info = next(item["info"] for item in body["stages"] if item["name"] == "metadata")
    assert "auto_note" not in info


def test_actual_video_and_tabs_render_without_chapters():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for this UI consumer check")
    script = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync('reel/web/static/app.js','utf8');
const start=source.indexOf('function renderVideo(d)');
const end=source.indexOf('function renderLogin(', start);
const nodes=new Map();
const get=(selector)=>{if(!nodes.has(selector))nodes.set(selector,{innerHTML:'',addEventListener(){}});return nodes.get(selector);};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const context={S:{quality:'preview'},$:get,esc,ic:()=>'',clock:String,fmtDur:String,
  set:(selector,html)=>{get(selector).innerHTML=html;},document:{querySelectorAll:()=>[]},
  badge:String,pct:()=>0,PUB:{youtube:['yt','YouTube'],playlist:['layers','Playlist'],slack:['hash','Slack'],classroom:['cap','Classroom'],library:['book','Library']},CUT_KIND:{},LBL:{}};
vm.createContext(context);require(require('path').resolve('tests/browser_locale.cjs')).install(context);vm.runInContext(source.slice(start,end),context);
for(const role of ['viewer','admin']){
 context.S.role=role;
 context.S.detail={id:1,has_video:true,has_preview:true,has_full:true,privacy_passed:true,has_thumbnail:false,
  metadata:{title:'Դաս՝ Փորձնական, 2026-11-19',description:'Դասի տեսագրություն։',youtube_description:'Դասի տեսագրություն։',chapters:[]},
  stages:[],segments:{keep:[[0,3000]],cuts:[],labels:[],original_sec:3000,final_sec:3000,cut_ratio:0,unclear_ratio:1},
  privacy:null,faces:[],publications:{youtube:null,playlist:null,slack:null,classroom:null,library:null}};
 context.renderVideo(context.S.detail);
 assert(get('#jd-video').innerHTML.includes('<video'));
 assert(!get('#jd-video').innerHTML.includes('class="chap"'));
 for(const tab of ['meta','cuts','privacy','pub']){context.S.tab=tab;context.renderTabs();assert(get('#jd-tabs').innerHTML.length>0);}
}
console.log('OK: actual video and all tabs accept chapterless metadata for viewer/admin');
'''
    result = subprocess.run([node, "-"], input=script, text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[1], timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
