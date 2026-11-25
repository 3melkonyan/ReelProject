"""Local assets, AA semantic tokens, theme persistence and role-safe navigation."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'reel/web/static'
NODE = shutil.which('node') or 'node'


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.links.append(attrs['src'])
        if tag == 'link' and attrs.get('href'):
            self.links.append(attrs['href'])


def test_all_entrypoint_manifest_and_css_resources_are_local_and_exist():
    parser = Resources()
    parser.feed((STATIC / 'index.html').read_text())
    manifest = json.loads((STATIC / 'manifest.webmanifest').read_text())
    urls = parser.links + [i['src'] for i in manifest['icons']]
    urls += re.findall(r'url\(["\']?([^\)"\']+)', (STATIC / 'app.css').read_text())
    for url in urls:
        assert url.startswith('/') and not url.startswith('//'), url
        if url == '/i18n-data.js':
            assert '@app.get("/i18n-data.js"' in (STATIC.parent / 'localization.py').read_text()
        else:
            assert (STATIC / url.lstrip('/')).is_file(), url
    assert 'Noto Sans Armenian' in (STATIC / 'app.css').read_text()
    assert (STATIC / 'brand/OFL-NotoSansArmenian.txt').is_file()


def tokens(dark=False):
    css = (STATIC / 'app.css').read_text()
    block = re.search(r':root\s*\{(.*?)\}', css, re.S).group(1)
    result = dict(re.findall(r'(--[\w-]+):([^;]+);', block))
    if dark:
        block = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', css, re.S).group(1)
        result.update(re.findall(r'(--[\w-]+):([^;]+);', block))
    def resolve(name):
        value = result[name].strip()
        return resolve(value[4:-1]) if value.startswith('var(') else value
    return resolve


def luminance(color):
    value = color.lstrip('#')
    rgb = [int(value[i:i+2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    return sum(c*w for c, w in zip(rgb, (.2126, .7152, .0722)))


@pytest.mark.parametrize('dark', [False, True])
@pytest.mark.parametrize('foreground,background', [
    *[(text, bg) for text in ('--text', '--muted', '--faint') for bg in ('--bg', '--surface', '--surface-2')],
    ('--on-primary', '--primary'), ('--on-primary', '--primary-hover'),
    ('--on-accent', '--accent'), ('--on-accent', '--accent-hover'),
    ('--accent', '--accent-soft'), ('--ok', '--ok-soft'),
    ('--warn', '--warn-soft'), ('--err', '--err-soft'), ('--info', '--info-soft'),
    ('--hero-ink', '--hero-bg'), ('--hero-muted', '--hero-bg'),
])
def test_semantic_text_pairs_meet_wcag_aa(dark, foreground, background):
    color = tokens(dark)
    a, b = sorted([luminance(color(foreground)), luminance(color(background))])
    assert (b + .05) / (a + .05) >= 4.5, (dark, foreground, background)


@pytest.mark.parametrize('dark', [False, True])
@pytest.mark.parametrize('foreground', ['--focus', '--input-border'])
def test_focus_and_control_boundaries_meet_three_to_one(dark, foreground):
    color = tokens(dark)
    a, b = sorted([luminance(color(foreground)), luminance(color('--surface'))])
    assert (b + .05) / (a + .05) >= 3


def run_node(script):
    if not Path(NODE).is_file():
        pytest.skip('Node is not available')
    p = subprocess.run([NODE, '-e', script, str(STATIC / 'app.js')], capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stdout + p.stderr


def test_dark_default_manual_choice_storage_failure_and_no_rerender():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),stored=new Map(),meta={},root={dataset:{}},attrs={};
let broken=false;
const button={setAttribute:(k,v)=>attrs[k]=v},c={window:{matchMedia:()=>({matches:true})},
 document:{documentElement:root,querySelector:()=>meta},$:()=>button,ic:String,
 localStorage:{getItem:k=>{if(broken)throw Error();return stored.get(k)},setItem:(k,v)=>{if(broken)throw Error();stored.set(k,v)}},
 getComputedStyle:()=>({getPropertyValue:()=>root.dataset.theme==='dark'?'#241E2E':'#FFFFFF'}),
 route:()=>{throw Error('Theme must not rerender the player/form')}};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(source.slice(source.indexOf('function getTheme()'),source.indexOf('/* ---------- shell & routing ---------- */')),c);
c.applyTheme();assert.equal(root.dataset.theme,'dark');assert.equal(meta.content,'#241E2E');
c.window.matchMedia=()=>({matches:false});c.applyTheme();assert.equal(root.dataset.theme,'dark');
stored.set('reel-theme','light');c.applyTheme();assert.equal(root.dataset.theme,'light');
c.toggleTheme();assert.equal(stored.get('reel-theme'),'dark');assert.equal(attrs['aria-pressed'],'true');
stored.set('reel-theme','invalid');c.applyTheme();assert.equal(root.dataset.theme,'dark');
broken=true;c.applyTheme();assert.equal(root.dataset.theme,'dark');c.toggleTheme();assert.equal(root.dataset.theme,'light');
''')


def test_navigation_roles_and_current_page_remain_explicit():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const s=fs.readFileSync(process.argv[1],'utf8'),c={S:{role:'admin'},esc:String,ic:()=>'',isDark:()=>false};
c.canUpload=()=>['admin','uploader'].includes(c.S.role);
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(s.slice(s.indexOf('const navLink ='),s.indexOf('function route()')),c);
for(const role of ['admin','viewer','uploader']){
 c.S.role=role;const shell=c.shell('jobs'),html=shell.match(/<nav[\s\S]*?<\/nav>/)[0];
 assert.equal(html.includes('href="#/settings"'),role==='admin');
 assert.equal(html.includes('href="#/upload"'),role!=='viewer');
 assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
 assert(shell.includes('data-skip-main'));assert(html.includes('aria-label="Հիմնական նավիգացիա"'));
}
''')


def test_skip_link_does_not_change_route_and_clear_filters_resets_every_control():
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const listeners={},elements={'#app':{},'#main':{focus:()=>{elements.focused=true}},'#search':{value:'old'}};
const c={document:{querySelector:k=>elements[k],addEventListener:(k,fn)=>listeners[k]=fn},window:{addEventListener:()=>{}},URLSearchParams,history:{replaceState:(a,b,url)=>{c.location.hash=url}},location:{hash:'#/jobs'}};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
vm.runInContext('S.jobFilter="done";S.jobSearch="old";S.jobGroup="A";S.jobSubject="Math";viewJobs=async()=>{}',c);
(async()=>{
 let prevented=false;
 await listeners.click({target:{closest:()=>({dataset:{skipMain:''}})},preventDefault:()=>prevented=true});
 assert(prevented&&elements.focused);assert.equal(c.location.hash,'#/jobs');
 await listeners.click({target:{closest:()=>({dataset:{clearFilters:''}})}});
 assert.equal(elements['#search'].value,'');assert.equal(c.location.hash,'#/jobs');
 assert.equal(vm.runInContext('[S.jobFilter,S.jobSearch,S.jobGroup,S.jobSubject].join("")',c),'');
})().catch(e=>{console.error(e);process.exitCode=1});
''')
