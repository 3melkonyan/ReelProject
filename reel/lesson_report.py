"""Post-completion lecturer email, delivered by the independent web monitor.

Existing lessons are not mass-mailed on first installation. The persistent start
watermark includes completions during later web downtime. No processing-worker edits.
"""
from __future__ import annotations
import json,re,time
from pathlib import Path
from reel import brand_mail
from reel.notifications import once


def output(conn,jid,stage):
    row=conn.execute('SELECT output FROM stages WHERE job_id=? AND name=?',(jid,stage)).fetchone()
    try:return json.loads(row[0]) if row and row[0] else {}
    except (ValueError,TypeError):return {}


def contents(conn,job,root):
    jid=job['id'];metadata=output(conn,jid,'metadata');segments=output(conn,jid,'segments')
    title=metadata.get('title') or f"Դաս {jid}"
    description=metadata.get('description') or ''
    # Metadata descriptions can contain chapters/URLs: keep the summary paragraph.
    summary=str(description).split('\n\n')[0][:2000] or 'Ամփոփումը հասանելի չէ։'
    cut=max(0,float(segments.get('original_sec') or 0)-float(segments.get('final_sec') or 0))/60
    questions=[]
    transcript=Path(root)/'work'/str(jid)/'transcript/transcript.json'
    try:
        data=json.loads(transcript.read_text());rows=data.get('segments',[]) if isinstance(data,dict) else data
        for row in rows:
            for text in re.split(r'(?<=[։.!?])\s+',str(row.get('text',''))):
                if ('՞' in text or '?' in text) and text.strip() not in questions:questions.append(text.strip()[:300])
    except (OSError,ValueError,TypeError):pass
    warnings=[]
    for row in conn.execute('SELECT name,output FROM stages WHERE job_id=?',(jid,)):
        try:result=json.loads(row[1] or '{}')
        except ValueError:continue
        if result.get('auto_note'):warnings.append(f"{row[0]} փուլը որակի նշում ունի․ մանրամասները ստուգեք վահանակում։")
        if row[0]=='render':
            try:
                if float(result.get('loudness_in',0))<-30:warnings.append('Սկզբնական ձայնը թույլ է եղել․ ստուգեք խոսքի հասկանալիությունը։')
            except (ValueError,TypeError):pass
            if 'fallback' in str(result.get('board','')).lower() or 'full frame kept' in str(result.get('board','')).lower():
                warnings.append('Կադրավորումը որոշ հատվածներում վերադարձել է ամբողջ կադրին։')
        if row[0]=='blur':
            warnings.append(f"Մշուշման կիրառումներ՝ {result.get('blurred_boxes',0)}, վստահ ուսանողի track՝ {result.get('student_tracks',0)}, անորոշ՝ {result.get('unsure_tracks',0)}։")
    return dict(title=title,summary=summary,minutes_cut=round(cut,1),questions=questions[:10],warnings=warnings[:15])


def poll(conn,root,*,send=None,now=None):
    now=time.time() if now is None else now
    conn.execute('CREATE TABLE IF NOT EXISTS service_watermarks(key TEXT PRIMARY KEY,value REAL NOT NULL)')
    conn.execute("INSERT OR IGNORE INTO service_watermarks VALUES('lecturer_reports',?)",(now,))
    start=conn.execute("SELECT value FROM service_watermarks WHERE key='lecturer_reports'").fetchone()[0]
    conn.execute('CREATE TABLE IF NOT EXISTS lecturer_report_recipients(job_id INTEGER PRIMARY KEY REFERENCES jobs(id),addresses TEXT NOT NULL)')
    lecturers=conn.execute("SELECT email,groups_json FROM users WHERE role='lecturer' AND active=1 AND removed_at IS NULL AND deleted_at IS NULL").fetchall()
    count=0
    for job in conn.execute("SELECT * FROM jobs WHERE status='done' AND (julianday(updated_at)-2440587.5)*86400>=? ORDER BY id",(start,)).fetchall():
        eligible=[]
        for lecturer in lecturers:
            try:
                if job['grp'] in json.loads(lecturer['groups_json']):eligible.append(lecturer['email'])
            except (ValueError,TypeError):pass
        conn.execute('INSERT OR IGNORE INTO lecturer_report_recipients VALUES(?,?)',(job['id'],json.dumps(eligible)))
        snapshot=set(json.loads(conn.execute('SELECT addresses FROM lecturer_report_recipients WHERE job_id=?',(job['id'],)).fetchone()[0]))
        report=None
        for lecturer in lecturers:
            if lecturer['email'] not in snapshot:continue
            try:groups=json.loads(lecturer['groups_json'])
            except (ValueError,TypeError):continue
            if job['grp'] not in groups:continue
            if report is None:report=contents(conn,job,root)
            def deliver():
                if send:return send(lecturer['email'],job,report)
                return brand_mail.send(root,lecturer['email'],'Reel․ դասի մշակման հաշվետվություն',
                    eyebrow='ԴԱՍԻ ՀԱՇՎԵՏՎՈՒԹՅՈՒՆ',title=report['title'],
                    paragraphs=[f"{job['grp']} · {job['subject'] or ''}",report['summary'],f"Կրճատվել է {report['minutes_cut']} րոպե։"],
                    sections=[('Տրանսկրիպտում հայտնաբերված հարցեր',report['questions'] or ['Հարցեր չեն հայտնաբերվել կամ տրանսկրիպտն այլևս հասանելի չէ։']),
                              ('Որակի նշումներ',report['warnings'] or ['Լրացուցիչ զգուշացում չկա։'])],
                    note='Հարցերն ավտոմատ ընտրված են ճանաչված խոսքից և կարող են սխալներ ունենալ։ Ստուգեք դասը վահանակում։')
            count+=int(once(conn,f'lesson-report:{job["id"]}',lecturer['email'],'email',deliver))
    return count
