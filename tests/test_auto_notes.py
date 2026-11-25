"""Summaries and passive UI notes; source-only fixtures never import the web app."""

from __future__ import annotations

import ast
import copy
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stage_summary():
    # Compile only the pure helper: no secrets, app bootstrap, database or worker.
    source = (PROJECT / "reel" / "web" / "app.py").read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_stage_summary"
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<isolated-stage-summary>", "exec"),
        namespace,
    )
    return namespace["_stage_summary"]


@pytest.mark.parametrize(
    "name,output,expected",
    [
        (
            "join",
            {"parts": 2, "mode": "copy", "joined_sec": 61},
            {"parts": 2, "mode": "copy", "joined_sec": 61},
        ),
        ("probe", {"duration_sec": 1800}, {"duration_sec": 1800}),
        (
            "transcribe",
            {"speed_x": 5, "segments": 20, "elapsed_sec": 2},
            {"speed_x": 5, "segments": 20, "elapsed_sec": 2},
        ),
        (
            "segments",
            {"cut_ratio": 0, "unclear_ratio": 0.9},
            {"cut_ratio": 0, "unclear_ratio": 0.9},
        ),
        ("render", {"lufs": -16}, {"lufs": -16}),
        ("preview", {"size_mb": 12, "skipped": False}, {"size_mb": 12, "skipped": False}),
        (
            "blur",
            {"lecturer_mode": "auto", "fps_processed": 80},
            {"lecturer_mode": "auto", "fps_processed": 80},
        ),
        ("privacy", {"other_faces": 0, "warnings": 1}, {"other_faces": 0, "warnings": 1}),
        ("metadata", {"title": "Հայերեն դաս"}, {"title": "Հայերեն դաս"}),
        (
            "youtube_upload",
            {"url": "https://example.com", "reused": True},
            {"url": "https://example.com", "reused": True},
        ),
        ("slack_post", {"reused": True}, {"reused": True}),
        ("classroom_post", {"reused": True, "skipped": True}, {"reused": True, "skipped": True}),
        ("unknown-future-stage", {}, {}),
    ],
)
def test_any_stage_exposes_string_note_without_widening_existing_summary(
    stage_summary, name, output, expected
):
    note = "unclear windows (90%) — nothing cut"
    output = {**output, "auto_note": note, "private_debug": {"secret": "not public"}}
    before = copy.deepcopy(output)
    assert stage_summary(name, output) == {**expected, "auto_note": note}
    assert output == before


@pytest.mark.parametrize("note", [None, 0, True, [], {"raw": "private debug"}])
def test_nonstring_note_is_not_forwarded(stage_summary, note):
    assert stage_summary("metadata", {"title": "Դաս", "auto_note": note}) == {"title": "Դաս"}
    assert stage_summary("unknown", {"auto_note": note}) == {}


def test_absent_and_empty_note_do_not_change_other_fields(stage_summary):
    assert stage_summary("metadata", {"title": "Դաս"}) == {"title": "Դաս"}
    assert stage_summary("unknown", {}) == {}
    assert stage_summary("unknown", {"auto_note": ""}) == {"auto_note": ""}


def test_auto_note_coexists_with_sanitized_board_framing(stage_summary):
    assert stage_summary(
        "render",
        {
            "lufs": -16,
            "board": "framed",
            "board_agree": 0.9,
            "framing": {"zoom": 1.2, "rotate_deg": 0, "raw": "private"},
            "auto_note": "future decision",
            "private_debug": "not public",
        },
    ) == {
        "lufs": -16,
        "board": "framed",
        "board_agree": 0.9,
        "framing": {"zoom": 1.2, "rotate_deg": 0},
        "auto_note": "future decision",
    }


def test_javascript_notes_and_polling_are_passive_for_both_roles():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable for isolated JavaScript checks")
    script = r"""
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("reel/web/static/app.js", "utf8");
const start = source.indexOf("function boardFramingLine(info)"), end = source.indexOf("\nfunction renderVideo(d)", start);
assert(start >= 0 && end > start);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const html = new Map();
let detail, videoCalls = 0, tabCalls = 0, requests = 0;
const context = {esc, S:{role:"viewer",sig:null}, $:()=>({}), api:async()=>{requests++;return detail},
 set:(selector,value)=>html.set(selector,value),ic:()=>"",badge:()=>"",rel:()=>"",fmtDur:String,
 renderVideo:()=>videoCalls++,renderTabs:()=>tabCalls++};
vm.createContext(context);require(require('path').resolve('tests/browser_locale.cjs')).install(context);
// viewJob now also displays stage progress; load its real pure helper, not a mock implementation.
vm.runInContext(source.slice(source.indexOf("const pct ="),source.indexOf("const badge =")),context);
vm.runInContext(source.slice(start,end),context);
const line = context.autoDecisionLine;
assert.equal(typeof line,"function");
for(const value of [undefined,null,{}, {auto_note:null},{auto_note:[]},{auto_note:5},{auto_note:""},{auto_note:"   "},Object.create({auto_note:"inherited"})])assert.equal(line(value),"");
const samples = [
 ["unclear windows 90% — nothing cut","ամբողջությամբ"],
 ["plan cut 70% — nothing cut","ամբողջությամբ"],
 ["planned result too short (50s) — nothing cut","ամբողջությամբ"],
 ["grounded chapters unavailable — default metadata","առանց գլուխների"],
 ["LLM metadata unavailable — default metadata","հայերեն"],
 ["LLM chapter wording unusable — grounded chapters kept","տրանսկրիպտից"]
];
for(const [note,meaning] of samples){
 const result=line({auto_note:note});
 assert(result.includes("🤖 Ավտոմատ որոշում՝ "));
 assert(result.includes(meaning));
 assert(!result.includes("nothing cut")&&!result.includes("default metadata")&&!result.includes("LLM"));
 assert.equal((result.match(/<div\b/g)||[]).length,1);
 assert.equal((result.match(/<\/div>/g)||[]).length,1);
 assert(!/<(?:button|input|select|a)\b/.test(result));
}
const unknown='unknown English <img src=x onerror="bad()"> & detail';
assert(line({auto_note:unknown}).includes(esc(unknown)));
assert(!line({auto_note:unknown}).includes("<img"));
// Date editor has its own tests; keep this fixture focused on passive stage notes.
context.renderLessonDate=()=>{};
async function run(){
 for(const role of ["viewer","admin"]){
  detail={id:9,status:"done",group:"fixture",file:"fixture.mp4",publications:{youtube:null},faces:[],
   stages:["segments","metadata","future-stage"].map(name=>({name,label:name,status:"done",attempts:1,info:{auto_note:"unclear windows 90% — nothing cut"}}))};
  context.S.role=role;context.S.sig=null;videoCalls=tabCalls=requests=0;
  await context.viewJob(9);
  assert.equal((html.get("#jd-steps").match(/🤖 Ավտոմատ որոշում՝/g)||[]).length,3);
  const signature=context.S.sig;
  detail.stages[0].info.auto_note="LLM metadata unavailable — default metadata";
  await context.viewJob(9);
  assert(html.get("#jd-steps").includes("հայերեն"));
  assert.equal(context.S.sig,signature);assert.equal(videoCalls,1);assert.equal(tabCalls,1);assert.equal(requests,2);
  detail.stages.forEach(stage=>{delete stage.info.auto_note});
  await context.viewJob(9);
  assert(!html.get("#jd-steps").includes("🤖 Ավտոմատ որոշում՝"));
  assert.equal(videoCalls,1);
 }
 console.log("OK: generic passive notes, six Armenian mappings, escaping, roles, polling without player reset");
}
run().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(
        [node, "-"], input=script, text=True, capture_output=True, cwd=PROJECT, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: generic passive notes" in result.stdout
