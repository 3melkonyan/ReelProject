"""Optional G40/1 integration never invents a player or leaks a foreign demo card."""
from contextlib import closing
from types import ModuleType
import sys
from reel.db import DB
from reel.library_view import library_entries


def test_demo_library_is_optional_scoped_and_metadata_only(tmp_path,monkeypatch):
    db=DB(tmp_path/'test.db')
    with closing(db.conn):
        assert library_entries(db,groups=[])==[]
        db.conn.execute('CREATE TABLE IF NOT EXISTS demo_lessons(id TEXT PRIMARY KEY)')  # G-40/1 schema may already create it
        fake=ModuleType('reel.demo')
        fake.read_demo_lessons=lambda database,groups=None:[{
            'id':'lesson-'+group,'group':group,'subject':'Python','title':'Դեմո դաս','description':'Նկարագրություն',
            'lesson_date':'2026-11-20','thumbnail':'https://untrusted.invalid/image','url':'https://youtu.be/abcdefghijk',
            'playable':True} for group in ('Group-A','Group-B')]
        monkeypatch.setitem(sys.modules,'reel.demo',fake)
        assert library_entries(db,groups=[])==[]
        cards=library_entries(db,groups={'Group-A'})
        assert len(cards)==1 and cards[0]['group']=='Group-A'
        assert cards[0]['demo'] and not cards[0]['playable'] and cards[0]['url'] is None
        assert cards[0]['job_id']=='demo:lesson-Group-A' and cards[0]['chapters']==[]
        assert cards[0]['thumbnail']=='/brand/reel-email.png'
        assert len(library_entries(db))==2
