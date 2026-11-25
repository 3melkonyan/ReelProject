"""Catalog parity, concurrent language scopes and browser message interpolation."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from string import Formatter

import pytest

from reel.config import validate_config
from reel.i18n import catalog, current_language, language_context, t

ROOT = Path(__file__).resolve().parents[1]


def test_catalogs_have_the_same_keys_and_placeholders():
    english, armenian = catalog("en"), catalog("hy")
    assert english.keys() == armenian.keys()
    assert english
    for key in english:
        assert english[key].strip() and armenian[key].strip(), key

        def fields(text):
            return {name for _, name, _, _ in Formatter().parse(text) if name is not None}

        assert fields(english[key]) == fields(armenian[key]), key


def test_armenian_default_explicit_english_and_scope_cleanup():
    assert current_language() == "hy"
    assert t("stage.join") == catalog("hy")["stage.join"]
    with language_context("en"):
        assert t("stage.join") == "Join recording parts"
        assert t("stage.join", language="hy") == catalog("hy")["stage.join"]
        with pytest.raises(RuntimeError), language_context("hy"):
            raise RuntimeError("unwind")
        assert current_language() == "en"
    assert current_language() == "hy"
    with pytest.raises(KeyError):
        t("unknown.message")
    with pytest.raises(ValueError):
        catalog("../en")


def test_concurrent_requests_keep_their_language():
    async def render(language):
        with language_context(language):
            await asyncio.sleep(0)
            return t("stage.join")

    async def both():
        return await asyncio.gather(render("en"), render("hy"))

    assert asyncio.run(both()) == [catalog("en")["stage.join"], catalog("hy")["stage.join"]]
    assert current_language() == "hy"


@pytest.mark.parametrize("language", ["en", "hy"])
def test_config_accepts_supported_ui_languages(language):
    config = {
        "ui_language": language,
        "groups": {"Sample": {"slack_channel": "C_SAMPLE", "playlist_id": ""}},
        "operator_slack_channel": "C_OPERATOR",
        "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
    }
    assert validate_config(config)["ui_language"] == language
    for invalid in ("fr", "", None, [], 1):
        with pytest.raises(ValueError, match="ui_language"):
            validate_config({**config, "ui_language": invalid})


def test_browser_catalogs_and_interpolation_match_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser tests")
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const messages = JSON.parse(process.argv[2]);
const saved = new Map();
const context = {localStorage: {setItem(k,v) { saved.set(k,v); }}, window: {REEL_I18N: {language: "hy", messages}}, document: {documentElement: {}, addEventListener() {}},
                 location: {protocol: "https:", reload() { this.reloaded = true; }}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
for (const [key, value] of Object.entries(messages)) {
  const values = Object.fromEntries([...value.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map(m => [m[1], "sample"]));
  assert.equal(context.t(key, values), value.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, "sample"));
}
messages["test.values"] = "Hello {name}";
assert.equal(context.t("test.values", {name: "<Sam>"}), "Hello <Sam>");
assert.throws(() => context.t("test.values"), /Missing message value/);
assert.throws(() => context.t("unknown"), /Unknown message/);
await assert.rejects(context.setLanguage("fr"), /Unsupported language/);
await context.setLanguage("hy");
assert(context.document.cookie.includes("reel-language=hy"));
assert(context.document.cookie.includes("; Secure"));
assert(context.location.reloaded);
assert.equal(saved.get("reel-language"), "hy");
context.S = {user: {id: 1}};
let request;
context.fetch = async (url, options) => { request = {url, options}; return {ok: true}; };
await context.setLanguage("en");
assert.equal(request.url, "/api/accounts/language");
assert.deepEqual(JSON.parse(request.options.body), {language: "en"});
context.location.reloaded = false;
context.fetch = async () => ({ok: false});
await assert.rejects(context.setLanguage("hy"));
assert(!context.location.reloaded);
context.R = {rec: {state: "recording"}};
await assert.rejects(context.setLanguage("en"));
"""
    script = (
        "(async()=>{\n"
        + script
        + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    )
    subprocess.run(
        [
            node,
            "-e",
            script,
            str(ROOT / "reel/web/static/i18n.js"),
            json.dumps(dict(catalog("hy")), ensure_ascii=False),
        ],
        check=True,
    )


def test_stage_labels_follow_the_active_language():
    from reel.web.labels import localized_stages, stage_labels

    assert stage_labels()["join"] == catalog("hy")["stage.join"]
    with language_context("hy"):
        assert stage_labels()["join"] == catalog("hy")["stage.join"]
        assert dict(localized_stages(language="en"))["join"] == "Join recording parts"
    with pytest.raises(ValueError):
        t("stage.join", language="")


def test_email_builders_can_render_both_languages_without_leaking_scope():
    from reel import brand_mail, student_mail
    from reel.web import account_mail

    for language in ("hy", "en"):
        message = account_mail.build_message(
            "reset",
            "person@example.test",
            "https://example.test/#/reset/token",
            "Person",
            language=language,
        )
        html = message.get_body(preferencelist=("html",)).get_content()
        assert f'lang="{language}"' in html
        assert ("Reset your password" in html) == (language == "en")
        assert ("Վերականգնեք Ձեր գաղտնաբառը" in html) == (language == "hy")
        branded = brand_mail.build(
            "person@example.test", "Report", eyebrow="Report", title="Lesson", language=language
        )
        assert f'lang="{language}"' in branded.get_body(preferencelist=("html",)).get_content()
        learning = student_mail.build_message(
            "new_lessons",
            "person@example.test",
            [
                {
                    "job_id": 1,
                    "title": "A lesson",
                    "group": "Sample",
                    "subject": "Python",
                    "url": "https://youtu.be/abcdefghijk",
                }
            ],
            "https://example.test",
            language=language,
        )
        assert ("A new lesson is available" in learning["Subject"]) == (language == "en")
    assert current_language() == "hy"


def test_content_language_settings_are_independent_of_ui():
    config = {
        "ui_language": "hy",
        "transcription_language": "en",
        "metadata_language": "en",
        "transcription_prompt": None,
        "groups": {"Sample": {"slack_channel": "C_SAMPLE", "playlist_id": ""}},
        "operator_slack_channel": "C_OPERATOR",
        "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4},
    }
    result = validate_config(config)
    assert result["ui_language"] == "hy" and result["metadata_language"] == "en"
    for changes in (
        {"metadata_language": "fr"},
        {"transcription_language": "../en"},
        {"transcription_prompt": False},
    ):
        with pytest.raises(ValueError):
            validate_config(config | changes)


def test_english_metadata_keeps_content_independent_of_interface(tmp_path, monkeypatch):
    from reel.stages import metadata

    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps({"segments": [{"start": 0, "end": 300, "text": "Functions and classes"}]})
    )
    monkeypatch.setattr(
        metadata.llm,
        "chat_json",
        lambda *a, **k: {
            "topic": "Functions",
            "summary": "Functions and classes.",
            "topic_changes": [],
        },
    )
    monkeypatch.setattr(
        metadata,
        "_chapter_plan",
        lambda *a: [
            {"id": "c0", "t": 0, "topic": "Functions"},
            {"id": "c1", "t": 100, "topic": "Classes"},
            {"id": "c2", "t": 200, "topic": "Objects"},
        ],
    )
    result = metadata.generate(transcript, [(0, 300)], tmp_path / "result", language="en")
    assert result["title"] == "Functions, Classes, Objects"
    assert result["chapters"][1]["title"] == "Classes"
    assert current_language() == "hy"
    monkeypatch.setattr(
        metadata.llm,
        "chat_json",
        lambda *a, **k: (_ for _ in ()).throw(metadata.llm.LLMError("offline")),
    )
    fallback = metadata.generate(transcript, [(0, 300)], tmp_path / "fallback", language="en")
    assert fallback["title"].startswith("Lesson: ")
    assert fallback["description"].startswith("Lesson recording.")


def test_web_sources_keep_armenian_copy_in_catalogs():
    import re

    files = list((ROOT / "reel/web").glob("*.py"))
    files += [
        p for p in (ROOT / "reel/web/static").iterdir() if p.suffix in (".js", ".html", ".css")
    ]
    for path in files:
        assert not re.search("[\u0530-\u058f]", path.read_text()), path


@pytest.mark.parametrize("language,heading", [("hy", "Բարի վերադարձ"), ("en", "Welcome back")])
def test_login_renders_the_selected_language(language, heading):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser tests")
    script = r"""
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert/strict');
const elements=new Map();
function element(selector){if(!elements.has(selector))elements.set(selector,{setAttribute(){},dataset:{}});return elements.get(selector);}
const context={window:{},document:{querySelector:element,addEventListener(){},documentElement:{dataset:{theme:'dark'}}},
  clearInterval(){},setInterval(){},location:{hash:'#/login'},localStorage:{getItem(){return null;}}};
vm.createContext(context);require(path.resolve('tests/browser_locale.cjs')).install(context,process.argv[2]);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.renderLogin();
const html=element('#app').innerHTML;
assert(html.includes(process.argv[3]));
assert(html.includes('data-language'));
assert(html.includes('value="hy"'));
assert(html.includes('value="en"'));
"""
    subprocess.run(
        [node, "-e", script, str(ROOT / "reel/web/static/app.js"), language, heading], check=True
    )
