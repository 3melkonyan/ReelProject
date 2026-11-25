"""Shared library cards, with optional nonplayable demo metadata."""
from reel.insights import library_records


def library_entries(database, legacy=None, *, groups=None):
    entries=library_records(database,legacy,groups=groups)
    exists=database.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_lessons'").fetchone()
    if not exists:
        return entries
    try:
        from reel.demo import read_demo_lessons
    except ModuleNotFoundError as error:
        if error.name!='reel.demo':
            raise
        return entries
    allowed=None if groups is None else frozenset(groups)
    for item in read_demo_lessons(database,groups=allowed):
        if allowed is not None and item.get('group') not in allowed:
            continue
        day=str(item.get('lesson_date')or'')
        thumbnail=str(item.get('thumbnail')or'/brand/reel-email.png')
        if not thumbnail.startswith('/')or thumbnail.startswith('//'):
            thumbnail='/brand/reel-email.png'
        entries.append({'job_id':'demo:'+str(item['id']),'group':item['group'],'subject':item.get('subject'),
                        'title':str(item.get('title')or'Ցուցադրական դաս'),
                        'description':str(item.get('description')or''),'lesson_date':day,
                        'published_at':day+'T00:00:00+00:00','thumbnail':thumbnail,
                        'lesson_no':None,'duration':item.get('duration_sec'),'chapters':[],'url':None,
                        'demo':True,'playable':False})
    return sorted(entries,key=lambda item:str(item.get('published_at')or''),reverse=True)
