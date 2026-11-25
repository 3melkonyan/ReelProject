from test_brand import run_node


def test_late_dashboard_response_cannot_replace_a_new_page():
    run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');let root={},release,writes=0;
const pending=new Promise(resolve=>release=resolve);
const c={$:()=>root,loadSummary:()=>pending,api:()=>pending,set:()=>writes++,S:{role:'admin',user:{id:1}},location:{hash:'#/'}};vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(source.slice(source.indexOf('async function viewHome()'),source.indexOf('const feedRow =')),c);
(async()=>{const old=c.viewHome();root={};release({counts:{},stages:[]});await old;assert.equal(writes,0);})().catch(e=>{console.error(e);process.exitCode=1;});
""")
