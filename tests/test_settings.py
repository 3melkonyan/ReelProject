"""General settings are private, group-free and tested against temporary roots only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from reel.db import DB
from reel import thumbnail
from reel.web import accounts
from reel.web.system_settings import DEFAULT_STYLE_KEY, make_router
from reel.web import system_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # Any unintended production DB/env access fails before opening it.
    original = sqlite3.connect
    def connect(path, *args, **kwargs):
        from urllib.parse import unquote, urlsplit
        value = unquote(urlsplit(str(path)).path) if str(path).startswith("file:") else str(path)
        assert Path(value).resolve().is_relative_to(tmp_path), "production DB access"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", connect)
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *args, **kwargs: pytest.fail("actual dotenv read"))
    cfg = {"SMTP_HOST": "private-host", "SMTP_USER": "private-user", "SMTP_PASSWORD": "private-secret", "SMTP_FROM": "private-sender"}
    monkeypatch.setattr(accounts, "settings", lambda root: dict(cfg))
    monkeypatch.setattr(accounts, "public_base", lambda root: "https://reel.example.test")
    monkeypatch.setattr(system_settings, "load_config", lambda path: {
        "groups": {"private-group": {"private": "never-leak"}}, "operator_slack_channel": "CFAKE",
        "cut": {"min_silence_sec": 120, "max_cut_ratio": .4, "private": "never-leak"},
        "time_savings": {"editing_min_per_hour": 30, "publishing_min_per_lesson": 5, "private": "never-leak"},
        "private": "never-leak"})
    with closing(DB(tmp_path / "reel.db").conn) as conn:
        for ident, role in ((1, "admin"), (2, "student"), (3, "lecturer")):
            conn.execute("INSERT INTO users(id,login,email,name,password_hash,role,created_at) VALUES(?,?,?,?,?,?,?)",
                         (ident, "user-" + str(ident), f"user{ident}@example.test", "Փորձնական", "!unusable", role, "2026-11-21T00:00:00+00:00"))
        conn.execute("INSERT INTO account_settings VALUES('unrelated','preserved')")
    env = {"ROOT": tmp_path}
    def database(): return DB(env["ROOT"] / "reel.db")
    def administrator(request):
        role = request.headers.get("test-role")
        if not role: raise HTTPException(401, "login required")
        if role != "admin": raise HTTPException(403, "admin only")
        if request.headers.get("x-requested-with") != "reel": raise HTTPException(400, "bad request")
        return role
    env.update(db=database, admin=administrator)
    app = FastAPI()
    @app.middleware("http")
    async def identity(request: Request, call_next):
        uid = request.headers.get("test-user")
        request.state.principal = {"id": int(uid), "role": request.headers.get("test-role")} if uid else None
        return await call_next(request)
    app.include_router(make_router(env))
    with TestClient(app) as client:
        yield type("Fixture", (), {"root": tmp_path, "client": client, "headers": {"test-role": "admin", "test-user": "1", "X-Requested-With": "reel"},
                                    "cfg": cfg, "env": env})


def test_get_only_safe_statuses_styles_and_no_group_or_secret_data(settings):
    result = settings.client.get("/api/admin/system-settings", headers=settings.headers)
    assert result.status_code == 200
    data = result.json()
    assert set(data) == {"email", "public_url", "legacy_enabled", "default_thumbnail_style", "thumbnail_styles", "advanced"}
    assert data["email"] == {"configured": True} and data["public_url"] == "https://reel.example.test"
    assert data["legacy_enabled"] is True and data["default_thumbnail_style"] == 0
    assert len(data["thumbnail_styles"]) == len(thumbnail.STYLES)
    assert all(set(item) == {"id", "preview"} for item in data["thumbnail_styles"])
    assert not any(value in json.dumps(data) for value in settings.cfg.values())
    assert "private-group" not in json.dumps(data) and "never-leak" not in json.dumps(data)
    assert data["advanced"] == {"operator_slack_channel": "CFAKE", "cut": {"min_silence_sec": 120, "max_cut_ratio": .4},
                                 "time_savings": {"editing_min_per_hour": 30, "publishing_min_per_lesson": 5}}
    assert not (settings.root / "thumbnail_styles.json").exists()
    with closing(DB(settings.root / "reel.db").conn) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM account_settings")] == [("unrelated", "preserved")]


@pytest.mark.parametrize("headers,expected", [({}, 401), ({"test-role": "student", "test-user": "2", "X-Requested-With": "reel"}, 403),
    ({"test-role": "lecturer", "test-user": "3", "X-Requested-With": "reel"}, 403),
    ({"test-role": "admin", "X-Requested-With": "reel"}, 401), ({"test-role": "admin", "test-user": "1"}, 400)])
def test_personal_admin_and_csrf_required_for_get_and_put(settings, headers, expected):
    assert settings.client.get("/api/admin/system-settings", headers=headers).status_code == expected
    assert settings.client.put("/api/admin/system-settings", headers=headers, json={"default_thumbnail_style": 2}).status_code == expected


def test_put_persists_only_global_style_and_preserves_other_settings(settings):
    response = settings.client.put("/api/admin/system-settings", headers=settings.headers, json={"default_thumbnail_style": 7})
    assert response.status_code == 200 and response.json()["default_thumbnail_style"] == 7
    with closing(DB(settings.root / "reel.db").conn) as conn:
        assert dict(conn.execute("SELECT * FROM account_settings")) == {"unrelated": "preserved", DEFAULT_STYLE_KEY: "7"}
    assert not (settings.root / "config.yaml").exists() and not (settings.root / "thumbnail_styles.json").exists()


@pytest.mark.parametrize("body", [{}, {"default_thumbnail_style": True}, {"default_thumbnail_style": "1"}, {"default_thumbnail_style": -1},
    {"default_thumbnail_style": 18}, {"default_thumbnail_style": 1, "groups": {}}, {"legacy_password": "never"}, [], None])
def test_put_rejects_nonallowlisted_or_invalid_values(settings, body):
    assert settings.client.put("/api/admin/system-settings", headers=settings.headers, json=body).status_code == 400


def test_malformed_oversized_body_and_stale_admin_cannot_write(settings):
    assert settings.client.put("/api/admin/system-settings", headers=settings.headers, content="[").status_code == 400
    assert settings.client.put("/api/admin/system-settings", headers=settings.headers, content="x" * 4097).status_code == 413
    with closing(DB(settings.root / "reel.db").conn) as conn:
        conn.execute("UPDATE users SET role='student' WHERE id=1")
    assert settings.client.put("/api/admin/system-settings", headers=settings.headers, json={"default_thumbnail_style": 3}).status_code == 403
    with closing(DB(settings.root / "reel.db").conn) as conn:
        assert not conn.execute("SELECT 1 FROM account_settings WHERE key=?", (DEFAULT_STYLE_KEY,)).fetchone()


def test_missing_email_public_address_and_invalid_saved_style_are_safe(settings, monkeypatch):
    settings.cfg.clear()
    monkeypatch.setattr(accounts, "public_base", lambda root: None)
    with closing(DB(settings.root / "reel.db").conn) as conn:
        conn.execute("INSERT INTO account_settings VALUES(?,?)", (DEFAULT_STYLE_KEY, "bad"))
        conn.execute("INSERT INTO account_settings VALUES('legacy_enabled','false')")
    data = settings.client.get("/api/admin/system-settings", headers=settings.headers).json()
    assert data["email"] == {"configured": False} and data["public_url"] is None
    assert data["default_thumbnail_style"] == 0 and data["legacy_enabled"] is False


def test_global_cover_default_is_read_only_and_keeps_explicit_assignments(settings, monkeypatch):
    styles = settings.root / "thumbnail_styles.json"
    styles.write_text('{"Explicit":3}')
    monkeypatch.setattr(thumbnail, "STYLES_FILE", styles)
    with closing(DB(settings.root / "reel.db").conn) as conn:
        conn.execute("INSERT INTO account_settings VALUES(?,?)", (DEFAULT_STYLE_KEY, "7"))
        assert thumbnail.group_style("Explicit") == 3
        assert thumbnail.group_style("New") == 7
        conn.execute("UPDATE account_settings SET value='4' WHERE key=?", (DEFAULT_STYLE_KEY,))
        assert thumbnail.group_style("Later") == 4
        assert thumbnail.group_style("New") == 7
    assert json.loads(styles.read_text()) == {"Explicit": 3, "New": 7, "Later": 4}


def test_missing_cover_default_keeps_previous_allocation_without_creating_db(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail, "STYLES_FILE", tmp_path / "thumbnail_styles.json")
    assert [thumbnail.group_style(name) for name in ("A", "B", "A")] == [0, 1, 0]
    assert not (tmp_path / "reel.db").exists()


def test_general_page_uses_no_group_endpoints_and_has_working_general_controls():
    from test_brand import run_node
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const code=fs.readFileSync(process.argv[1].replace('app.js','admin.js'),'utf8');
const elements={},calls=[];let dark=true;
function element(id){return elements[id] ||= {id,hidden:false,value:'',checked:true,disabled:false,dataset:{},textContent:'',_html:'',set innerHTML(v){this._html=v;for(const m of v.matchAll(/id="([^"]+)"/g))element(m[1]);},get innerHTML(){return this._html},querySelectorAll(){return []}};}
const data={email:{configured:true},public_url:'https://reel.example.test',legacy_enabled:true,default_thumbnail_style:2,
 thumbnail_styles:[{id:2,preview:'/api/admin/thumbnail-styles/2/preview.png'}],advanced:{operator_slack_channel:'CFIXTURE',
 cut:{min_silence_sec:120,max_cut_ratio:.4},time_savings:{editing_min_per_hour:30,publishing_min_per_lesson:5}}};
const c={S:{role:'admin',user:{id:1}},$:selector=>elements[selector.replace('#','')]||null,
 set:(selector,html)=>element(selector.slice(1)).innerHTML=html,esc:x=>String(x).replaceAll('<','&lt;'),
 isDark:()=>dark,toggleTheme:()=>dark=!dark,api:async(path,options)=>{calls.push({path,options});return data},navigator:{clipboard:{writeText:async()=>{}}}};
vm.createContext(c);vm.runInContext(code,c);
(async()=>{await c.viewSettings();
 assert.deepEqual(calls.map(x=>x.path),['/api/admin/system-settings']);
 const html=elements['settings-content'].innerHTML;
 assert.equal((html.match(/class="btn primary"/g)||[]).length,1);
 for(const label of ['Էլ․ նամակներ','Հանրային հասցե','Ընդհանուր մուտք','Լռելյայն շապիկ','Տեսք'])assert(html.includes(label));
 assert(!html.includes('#/groups')&&!html.includes('/admin/config')&&!html.includes('private-secret'));
 assert(!/\b(?:Playlist|ETA|DB|track|Worker|Email|thumbnail)\b/i.test(html.replace(/<[^>]*>/g,' ')));
 elements['settings-theme'].onchange({target:{value:'light'}});assert.equal(dark,false);
 await elements['settings-legacy-form'].onsubmit({preventDefault(){}});await new Promise(r=>setImmediate(r));
 assert.equal(calls[1].path,'/api/accounts/legacy');assert.deepEqual(JSON.parse(calls[1].options.body),{enabled:true});
 await elements['settings-style-form'].onsubmit({preventDefault(){}});await new Promise(r=>setImmediate(r));
 assert.equal(calls[2].path,'/api/admin/system-settings');assert.deepEqual(JSON.parse(calls[2].options.body),{default_thumbnail_style:2});
})().catch(e=>{console.error(e);process.exitCode=1});
''')
