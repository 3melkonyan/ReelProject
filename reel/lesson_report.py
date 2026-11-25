"""Post-completion lecturer email, delivered by the independent web monitor.

Existing lessons are not mass-mailed on first installation. The persistent start
watermark includes completions during later web downtime. No processing-worker edits.
"""
from __future__ import annotations

from reel.i18n import t as _t
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
    title=metadata.get('title') or f"{_t('ui.app.lesson')} {jid}"
    description=metadata.get('description') or ''
    # Metadata descriptions can contain chapters/URLs: keep the summary paragraph.
    summary=str(description).split('\n\n')[0][:2000] or _t('text.lesson_report.summary_unavailable')
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
        if result.get('auto_note'):warnings.append(f"{row[0]} {_t('text.lesson_report.has_a_quality_note_check_the_dashboard')}")
        if row[0]=='render':
            try:
                if float(result.get('loudness_in',0))<-30:warnings.append(_t('text.lesson_report.the_source_audio_was_quiet_check_speech'))
            except (ValueError,TypeError):pass
            if 'fallback' in str(result.get('board','')).lower() or 'full frame kept' in str(result.get('board','')).lower():
                warnings.append(_t('text.lesson_report.some_sections_fell_back_to_full_frame'))
        if row[0]=='blur':
            warnings.append(f"{_t('text.lesson_report.face_blurs')} {result.get('blurred_boxes', 0)}{_t('text.lesson_report.confident_student_tracks')} {result.get('student_tracks', 0)}{_t('text.lesson_report.uncertain')} {result.get('unsure_tracks', 0)}{_t('ui.accounts.punctuation')}")
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
        reports={}
        for lecturer in lecturers:
            if lecturer['email'] not in snapshot:continue
            try:groups=json.loads(lecturer['groups_json'])
            except (ValueError,TypeError):continue
            if job['grp'] not in groups:continue
            from reel.language import recipient_language
            from reel.i18n import language_context
            with language_context(recipient_language(root,lecturer['email'])):
                language=recipient_language(root,lecturer['email'])
                report=reports.setdefault(language,contents(conn,job,root))
                def deliver(lecturer=lecturer, job=job, report=report):
                    if send:return send(lecturer['email'],job,report)
                    return brand_mail.send(root,lecturer['email'],_t('text.lesson_report.reel_lesson_processing_report'),
                        eyebrow=_t('text.lesson_report.lesson_report'),title=report['title'],
                        paragraphs=[f"{job['grp']} · {job['subject'] or ''}",report['summary'],f"{_t('text.lesson_report.removed')} {report['minutes_cut']} {_t('text.lesson_report.minutes')}"],
                        sections=[(_t('text.lesson_report.questions_found_in_the_transcript'),report['questions'] or [_t('text.lesson_report.no_questions_were_found_or_the_transcript')]),
                                  (_t('text.lesson_report.quality_notes'),report['warnings'] or [_t('text.lesson_report.no_additional_warnings')])],
                        note=_t('text.lesson_report.questions_were_selected_automatically_from_the_transcript'))
                count+=int(once(conn,f'lesson-report:{job["id"]}',lecturer['email'],'email',deliver))
    return count
