"""Opaque CTA contrast, dark defaults and one Armenian label source."""
import re
from pathlib import Path
from test_brand import tokens,luminance,run_node,STATIC


def test_brand_button_exact_color_contrast_and_neutral_surfaces():
    for dark in (False,True):
        color=tokens(dark)
        assert color('--primary')=='#D500C8' and color('--on-primary')=='#FFFFFF'
        assert (luminance('#FFFFFF')+.05)/(luminance(color('--primary'))+.05)>=4.5
    dark=tokens(True)
    assert dark('--bg')=='#171717'
    for key in ('--bg','--surface','--surface-2','--border'):
        h=dark(key).lstrip('#');assert h[:2]==h[2:4]==h[4:6]
    css=(STATIC/'app.css').read_text()
    assert 'font-size:16px;font-weight:600' in css
    assert 'opacity:1' in css and '--light-rays:' in css


def test_every_stage_has_one_armenian_label_source():
    from reel.web.labels import STAGES,STAGE_LABEL
    from reel.stages import STAGES as pipeline
    assert [n for n,_ in STAGES]==[n for n,_ in pipeline]
    # Learning stages are inserted by name (captions, summary, student e-mails); the 17 original ones stay.
    assert len(STAGE_LABEL)==len(pipeline)>=17
    assert all(re.search('[Ա-Ֆա-ֆև]',label) and not re.search('[A-Za-z_]',label) for label in STAGE_LABEL.values())
    source=(STATIC.parent/'app.py').read_text()
    assert 'from reel.web.labels import STAGES, STAGE_LABEL' in source
    assert 'STAGES = [' not in source


def test_dark_before_paint_and_short_searches():
    source=(STATIC/'index.html').read_text()
    assert 'data-theme="dark"' in source
    assert "t==='dark'||t==='light'?t:'dark'" in source
    assert 'prefers-color-scheme: dark' not in source
    for file in ('app.js','insights.js'):
        text=(STATIC/file).read_text()
        placeholders=re.findall('placeholder="([^"\n]*)"',text)
        assert all(len(x)<=18 for x in placeholders if x.startswith(('Որոնել','Թեմա')))


def test_five_full_word_navigation_entries_and_header_help():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const s=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'admin'},esc:String,ic:()=>'',isDark:()=>true};
c.canUpload=()=>['admin','uploader'].includes(c.S.role);vm.createContext(c);
vm.runInContext(s.slice(s.indexOf('const navLink ='),s.indexOf('function route()')),c);
for(const role of ['admin','viewer','uploader','student']){
 c.S.role=role;const nav=c.navigation('jobs');
 const visible=nav.split('<details')[0];
 assert((visible.match(/<a /g)||[]).length+(nav.includes('<details')?1:0)<=5);
 assert(!nav.includes('#/about'));assert(!nav.includes('Կարգ.'));
 const shell=c.shell('jobs');assert(shell.includes('class="header-help"'));assert(shell.includes('aria-haspopup="menu"'));assert(shell.includes('aria-expanded="false"'));
}
''')


def test_explicitly_bundles_pinned_mardoto_with_both_notices():
    assert 'Mardoto-Bold.woff2' in (STATIC/'app.css').read_text()
    assert (STATIC/'brand/OFL-Mardoto.txt').is_file()
    assert (STATIC/'brand/Mardoto-Apache-2.0.txt').is_file()
