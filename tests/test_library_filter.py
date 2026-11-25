"""The library filter lists every permitted group and subject with lesson counts."""
from contextlib import closing

from reel.db import DB
from reel.learning import library_facets
from reel.web import accounts as a

PASSWORD = 'g40-library-password'
CONFIG = {'AI 2.6': {'subjects': [{'name': 'Python', 'playlist': 'PL1'}, {'name': 'Math', 'playlist': ''}]},
          'AI 2.5': {'subjects': [{'name': 'Python', 'playlist': 'PL2'}]},
          'AI 2.4': {'subjects': [{'name': 'Data Science', 'playlist': ''}]}}
RECORDS = [{'group': 'AI 2.5', 'subject': 'Python'}, {'group': 'AI 2.5', 'subject': 'Python'},
           {'group': 'Group-A', 'subject': 'Group-A'}]


def test_admin_sees_every_active_group_even_with_zero_lessons():
    data = library_facets(RECORDS, CONFIG, {'AI 2.4'}, None)
    by_name = {g['name']: g for g in data['groups']}
    assert by_name['AI 2.6']['lessons'] == 0
    assert by_name['AI 2.6']['subjects'] == [{'name': 'Python', 'lessons': 0}, {'name': 'Math', 'lessons': 0}]
    assert by_name['AI 2.5']['subjects'] == [{'name': 'Python', 'lessons': 2}]
    assert by_name['Group-A']['lessons'] == 1          # removed from config, lessons stay reachable
    assert 'AI 2.4' not in by_name                      # archived and empty: not offered to admins
    assert data['total'] == 3


def test_archived_group_with_lessons_and_own_scope():
    archived = library_facets([{'group': 'AI 2.4', 'subject': 'Data Science'}], CONFIG, {'AI 2.4'}, None)
    assert [(g['name'], g['archived'], g['lessons']) for g in archived['groups']][-1] == ('AI 2.4', True, 1)
    own = library_facets([], CONFIG, {'AI 2.4'}, frozenset({'AI 2.4'}))
    assert [(g['name'], g['archived']) for g in own['groups']] == [('AI 2.4', True)]
    student = library_facets([], CONFIG, set(), frozenset({'AI 2.6'}))
    assert [g['name'] for g in student['groups']] == ['AI 2.6']


def _user(web, login, role, groups):
    d = DB(web.root / 'reel.db')
    with closing(d.conn):
        a.create_user(d, login, PASSWORD, {'name': 'Փորձնական ' + role, 'email': login + '@example.test',
                                           'role': role, 'groups': groups})
    client = web.client()
    assert client.call('POST', '/api/login', body={'login': login, 'password': PASSWORD})[0] == 200
    return client


def test_http_facets_are_scoped_by_role(web):
    student = _user(web, 'g40-student', 'student', ['Group-B'])
    code, data = student.call('GET', '/api/library/facets')
    assert code == 200
    assert data['groups'] == [{'name': 'Group-B', 'lessons': 0, 'archived': False,
                               'subjects': [{'name': 'Python', 'lessons': 0}, {'name': 'JavaScript', 'lessons': 0}]}]
    admin = web.client('admin')
    assert [g['name'] for g in admin.call('GET', '/api/library/facets')[1]['groups']] == ['Group-A', 'Group-B']
    lecturer = _user(web, 'g40-lecturer', 'lecturer', ['Group-A'])
    assert [g['name'] for g in lecturer.call('GET', '/api/library/facets')[1]['groups']] == ['Group-A']
    assert web.client('uploader').call('GET', '/api/library/facets')[0] == 403
    assert web.client().call('GET', '/api/library/facets')[0] == 401


def test_ui_options_show_counts_and_lock_student_group():
    from test_brand import run_node
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[1].replace('app.js','insights.js'),'utf8');
function select(){const el={dataset:{},disabled:false,value:'',_html:'',options:[]};
 Object.defineProperty(el,'innerHTML',{get(){return el._html},set(v){el._html=v;
  el.options=[...v.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)].map(m=>({value:m[1],text:m[2]}));
  el.value=el.options[0]?el.options[0].value:''}});return el;}
const els={'#library-group':select(),'#library-subject':select()};
const c={S:{role:'student',libraryFacets:{groups:[{name:'AI 2.6',lessons:0,archived:false,
 subjects:[{name:'Python',lessons:0},{name:'Math',lessons:0}]}]}},esc:String,$:s=>els[s],
 window:{addEventListener(){}},document:{body:{}},MutationObserver:class{observe(){}}};
vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);vm.runInContext(source,c);
c.fillLibraryGroups();c.fillLibrarySubjects();
assert.deepEqual(els['#library-group'].options.map(o=>o.text),['AI 2.6 (0 դաս)']);
assert.equal(els['#library-group'].disabled,true);
assert.deepEqual(els['#library-subject'].options.map(o=>o.text),['Բոլոր առարկաները (0 դաս)','Python (0 դաս)','Math (0 դաս)']);
c.S.role='admin';c.S.libraryFacets={groups:[{name:'AI 2.5',lessons:2,archived:false,subjects:[{name:'Python',lessons:2}]},
 {name:'AI 2.6',lessons:0,archived:false,subjects:[{name:'Python',lessons:0},{name:'Math',lessons:0}]},
 {name:'AI 2.4',lessons:1,archived:true,subjects:[{name:'Data Science',lessons:1}]}]};
c.fillLibraryGroups();els['#library-group'].value='';c.fillLibrarySubjects();
assert.deepEqual(els['#library-group'].options.map(o=>o.text),['Բոլոր խմբերը (3 դաս)','AI 2.5 (2 դաս)','AI 2.6 (0 դաս)','AI 2.4 · ավարտված (1 դաս)']);
assert.equal(els['#library-group'].disabled,false);
assert.deepEqual(els['#library-subject'].options.map(o=>o.text),['Բոլոր առարկաները (3 դաս)','Data Science (1 դաս)','Math (0 դաս)','Python (2 դաս)']);
els['#library-group'].value='AI 2.6';c.fillLibrarySubjects();
assert.deepEqual(els['#library-subject'].options.map(o=>o.text),['Բոլոր առարկաները (0 դաս)','Python (0 դաս)','Math (0 դաս)']);
''')
