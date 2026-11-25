"""Read-only metadata cards for explicitly tagged demo lessons; never playable media."""

DEMO_TAG = "g40-demo-v1"


def read_demo_lessons(database, groups=None):
    """An empty scope returns no cards; None means an already-authorized all-groups caller."""
    allowed = None if groups is None else frozenset(groups)
    result = []
    for row in database.conn.execute(
            "SELECT d.*,u.name lecturer_name,u.avatar lecturer_avatar FROM demo_lessons d "
            "LEFT JOIN users u ON u.id=d.lecturer_id WHERE d.demo_tag=? ORDER BY d.lesson_date DESC,d.id", (DEMO_TAG,)):
        if allowed is not None and row["group_name"] not in allowed:
            continue
        lecturer = None
        if row["lecturer_id"] is not None and row["lecturer_name"] is not None:
            lecturer = {"id":row["lecturer_id"], "name":row["lecturer_name"],
                        "avatar":f"/api/accounts/avatars/{row['lecturer_id']}?v={row['lecturer_avatar']}" if row["lecturer_avatar"] else None}
        result.append({"id":row["id"], "group":row["group_name"], "subject":row["subject"],
                       "title":row["title"], "description":row["description"], "lesson_date":row["lesson_date"],
                       "thumbnail":row["thumbnail"], "lecturer":lecturer, "duration_sec":row["duration_sec"],
                       "demo":True, "playable":False, "has_video":False, "url":None})
    return result


def job_cards(database,groups=None):
    """Presentation-only cards. They never enter jobs/stages/publications or a worker queue."""
    from datetime import datetime,timezone
    result=[]
    for item in read_demo_lessons(database,groups):
        stamp=datetime.fromisoformat(item['lesson_date']).replace(tzinfo=timezone.utc).timestamp()
        result.append({**item,'id':'demo:'+item['id'],'file':'Ցուցադրական դաս','status':'done',
                       'created':stamp,'updated':stamp,'duration_sec':item['duration_sec'],'final_sec':item['duration_sec'],
                       'done_stages':0,'total_stages':0,'progress':100,'proc_sec':None,
                       'has_thumbnail':False,'has_video':False,'current_stage':None,'current_label':'Ցուցադրական',
                       'error':None,'dry':False,'youtube':None,'cut_ratio':0})
    return result
