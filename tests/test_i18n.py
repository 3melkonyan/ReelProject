"""Catalog parity, concurrent language scopes and browser message interpolation."""
import asyncio
import json
from pathlib import Path
import shutil
from string import Formatter
import subprocess

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
        fields = lambda text: {name for _, name, _, _ in Formatter().parse(text) if name is not None}
        assert fields(english[key]) == fields(armenian[key]), key


def test_english_default_explicit_armenian_and_scope_cleanup():
    assert current_language() == "en"
    assert t("stage.join") == "Join recording parts"
    with language_context("hy"):
        assert t("stage.join") == catalog("hy")["stage.join"]
        assert t("stage.join", language="en") == "Join recording parts"
        with pytest.raises(RuntimeError):
            with language_context("en"):
                raise RuntimeError("unwind")
        assert current_language() == "hy"
    assert current_language() == "en"
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
    assert current_language() == "en"


@pytest.mark.parametrize("language", ["en", "hy"])
def test_config_accepts_supported_ui_languages(language):
    config = {"ui_language": language, "groups": {"Sample": {"slack_channel": "C_SAMPLE", "playlist_id": ""}},
              "operator_slack_channel": "C_OPERATOR", "cut": {"min_silence_sec": 120, "max_cut_ratio": 0.4}}
    assert validate_config(config)["ui_language"] == language
    for invalid in ("fr", "", None, [], 1):
        with pytest.raises(ValueError, match="ui_language"):
            validate_config({**config, "ui_language": invalid})


def test_browser_catalogs_and_interpolation_match_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for browser tests")
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const messages = JSON.parse(process.argv[2]);
const context = {window: {REEL_I18N: {language: "hy", messages}}, document: {documentElement: {}},
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
assert.throws(() => context.setLanguage("fr"), /Unsupported language/);
context.setLanguage("hy");
assert(context.document.cookie.includes("reel-language=hy"));
assert(context.document.cookie.includes("; Secure"));
assert(context.location.reloaded);
'''
    subprocess.run([node, "-e", script, str(ROOT / "reel/web/static/i18n.js"),
                    json.dumps(dict(catalog("hy")), ensure_ascii=False)], check=True)


def test_stage_labels_follow_the_active_language():
    from reel.web.labels import localized_stages, stage_labels
    assert stage_labels()["join"] == "Join recording parts"
    with language_context("hy"):
        assert stage_labels()["join"] == catalog("hy")["stage.join"]
        assert dict(localized_stages(language="en"))["join"] == "Join recording parts"
    with pytest.raises(ValueError):
        t("stage.join", language="")
