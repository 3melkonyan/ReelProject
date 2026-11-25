"""Opaque CTA contrast, dark defaults and one Armenian label source."""

import ast
import json
import re

from test_brand import STATIC, luminance, run_node, tokens


def test_brand_button_exact_color_contrast_and_neutral_surfaces():
    for dark in (False, True):
        color = tokens(dark)
        assert color("--primary") == "#D500C8" and color("--on-primary") == "#FFFFFF"
        assert (luminance("#FFFFFF") + 0.05) / (luminance(color("--primary")) + 0.05) >= 4.5
    dark = tokens(True)
    assert dark("--bg") == "#171717"
    for key in ("--bg", "--surface", "--surface-2", "--border"):
        h = dark(key).lstrip("#")
        assert h[:2] == h[2:4] == h[4:6]
    css = (STATIC / "app.css").read_text()
    assert re.search(r"font-size:\s*16px;\s*font-weight:\s*600", css)
    assert re.search(r"opacity:\s*1", css) and "--light-rays:" in css


def test_every_stage_has_one_armenian_label_source(hy_locale):
    from reel.web.labels import localized_stages, stage_labels

    STAGES, STAGE_LABEL = localized_stages(), stage_labels()
    from reel.stages import STAGES as pipeline

    assert [n for n, _ in STAGES] == [n for n, _ in pipeline]
    # Learning stages are inserted by name (captions, summary, student e-mails); the 17 original ones stay.
    assert len(STAGE_LABEL) == len(pipeline) >= 17
    assert all(
        re.search("[Ա-Ֆա-ֆև]", label) and not re.search("[A-Za-z_]", label)
        for label in STAGE_LABEL.values()
    )
    source = (STATIC.parent / "app.py").read_text()
    tree = ast.parse(source)
    labels_import = next(
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "reel.web.labels"
    )
    assert {"localized_stages", "stage_labels"} <= {alias.name for alias in labels_import.names}
    assert not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "STAGES" for target in node.targets)
        for node in tree.body
    )


def test_dark_before_paint_and_short_searches():
    source = (STATIC / "index.html").read_text()
    assert 'data-theme="dark"' in source
    scripts = re.findall(r"<script>(.*?)</script>", source, re.S)
    run_node(
        "const vm=require('vm'), assert=require('assert/strict');"
        + "const scripts="
        + json.dumps(scripts)
        + ";"
        + "for(const saved of [null,'dark','light','invalid']) {"
        + "const context={localStorage:{getItem:key=>key==='reel-theme'?saved:null},document:{documentElement:{dataset:{}}}};"
        + "vm.createContext(context);scripts.forEach(script=>vm.runInContext(script,context));"
        + "assert.equal(context.document.documentElement.dataset.theme,saved==='light'?'light':'dark');}"
    )
    assert "prefers-color-scheme: dark" not in source
    for file in ("app.js", "insights.js"):
        text = (STATIC / file).read_text()
        placeholders = re.findall('placeholder="([^"\n]*)"', text)
        assert all(len(x) <= 18 for x in placeholders if x.startswith(("Որոնել", "Թեմա")))


def test_five_full_word_navigation_entries_and_header_help():
    run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const s=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'admin'},esc:String,ic:()=>'',isDark:()=>true};
c.canUpload=()=>['admin','uploader'].includes(c.S.role);vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(s.slice(s.indexOf('const navLink ='),s.indexOf('function route()')),c);
for(const role of ['admin','viewer','uploader','student']){
 c.S.role=role;const nav=c.navigation('jobs');
 const visible=nav.split('<details')[0];
 assert((visible.match(/<a /g)||[]).length+(nav.includes('<details')?1:0)<=5);
 assert(!nav.includes('#/about'));assert(!nav.includes('Կարգ.'));
 const shell=c.shell('jobs');assert(shell.includes('class="header-help"'));assert(shell.includes('aria-haspopup="menu"'));assert(shell.includes('aria-expanded="false"'));
}
""")


def test_explicitly_bundles_pinned_mardoto_with_both_notices():
    assert "Mardoto-Bold.woff2" in (STATIC / "app.css").read_text()
    assert (STATIC / "brand/OFL-Mardoto.txt").is_file()
    assert (STATIC / "brand/Mardoto-Apache-2.0.txt").is_file()
