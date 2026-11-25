"""Compact, cleanup-safe presentation data. Never changes editorial decisions."""
from __future__ import annotations

from reel.i18n import t as _t
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs
from reel.progress import seconds
from reel.lesson_dates import job_date

SAVINGS = {'editing_min_per_hour': 30, 'publishing_min_per_lesson': 5}


def _no_cut_info(plan):
    """Keep structured decisions authoritative; decode only known legacy guard notes."""
    code = plan.get('no_cut_code')
    note = plan.get('auto_note') if isinstance(plan.get('auto_note'), str) else ''
    matched = None
    for candidate, pattern in (
            ('unclear', r'^unclear windows ([0-9.]+)% \(> ([0-9.]+)%\)'),
            ('over_max_ratio', r'^plan cut ([0-9.]+)% \(> ([0-9.]+)%\)')):
        found = re.match(pattern, note.strip(), re.I)
        if found:
            matched = (candidate, found.groups())
            break
    if 'no_cut_code' not in plan:
        if re.match(r'^\d+ transcript segments have broken timestamps\b', note.strip(), re.I):
            code = 'broken_timestamps'
        elif matched:
            code = matched[0]
    if code == 'none_found':
        return code, _t('text.insights.no_off_topic_sections_or_long_breaks')
    if code == 'broken_timestamps':
        return code, _t('text.insights.speech_timestamps_were_invalid_no_content_was')
    if code in ('unclear', 'over_max_ratio'):
        details = plan.get('no_cut_details')
        values = (details.get('percent'), details.get('limit')) if isinstance(details, dict) else (None, None)
        if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 100 for v in values):
            try:
                values = tuple(float(v) for v in matched[1]) if matched and matched[0] == code else ()
            except ValueError:
                values = ()
        if len(values) == 2 and all(math.isfinite(v) and 0 <= v <= 100 for v in values):
            percent, limit = (f'{v:.2f}'.rstrip('0').rstrip('.') for v in values)
            if values[0] > values[1] and percent == limit:
                for digits in range(3, 18):
                    percent, limit = (f'{v:.{digits}f}'.rstrip('0').rstrip('.') for v in values)
                    if percent != limit:
                        break
                else:
                    percent, limit = map(str, values)  # Keep scientific exponents intact.
            if code == 'unclear':
                return code, f"{_t('text.insights.could_not_clearly_classify')} {percent}{_t('text.insights.of_the_lesson_limit')} {limit}{_t('text.insights.no_content_was_cut_2')}"
            return code, f"{_t('text.insights.planned_cuts_were')} {percent}{_t('text.insights.the_limit_was')} {limit}{_t('text.insights.no_content_was_cut')}"
    return code, _t('text.insights.no_cuts_were_made')


def cut_summary(plan, transcript=None, parts=None):
    original = plan.get('original_sec')
    final = plan.get('final_sec')
    cuts = []
    for c in plan.get('cuts', []):
        a, b = c['start'], c['end']
        words = [s.get('text', '') for s in (transcript or {}).get('segments', [])
                 if s.get('end', 0) > a and s.get('start', 0) < b]
        text = ' '.join(words).split()
        why = str(c.get('why', 'other'))
        kind = ('before' if a <= 2 else 'after' if original and b >= original-2 else
                'silence' if 'silence' in why.lower() else 'break' if 'break' in why.lower() else 'other')
        # Source times are already on the joined multipart timeline.
        boundary = sum(max(0, min(a, end)-start) for start,end in plan.get('keep', []) if start < a)
        cuts.append({**c, 'kind': kind, 'duration': b-a, 'first_words': ' '.join(text[:16]),
                     'last_words': ' '.join(text[-16:]), 'final_boundary': boundary})
    compact = {k: plan[k] for k in ('original_sec', 'final_sec', 'cut_ratio', 'auto_note', 'keep', 'no_cut_details') if k in plan}
    no_cut_code, no_cuts_reason = _no_cut_info(plan)
    return {**compact, 'no_cut_code': no_cut_code if not cuts else None,
            'details_available': 'cuts' in plan, 'cuts': cuts, 'parts': {k:v for k,v in (parts or {}).items() if k in ('parts','part_sec')},
            'cut_seconds': max(0, original-final) if original is not None and final is not None else None,
            'no_cuts_reason': (_t('text.insights.cut_details_were_not_saved') if 'cuts' not in plan else no_cuts_reason) if not cuts else None}


def video_id(url):
    parsed = urlparse(str(url or ''))
    if parsed.scheme != 'https': return None
    value = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else (
        parse_qs(parsed.query).get('v', [''])[0] if parsed.hostname in ('www.youtube.com', 'youtube.com') else '')
    return value if re.fullmatch(r'[A-Za-z0-9_-]{11}', value) and not value.startswith('dry') else None


def library_records(db, legacy=None, *, groups=None):
    old = {}
    if legacy and Path(legacy).is_file():
        for line in Path(legacy).read_text(encoding='utf-8').splitlines():
            try:
                e = json.loads(line); old[str(e['job_id'])] = e
            except (ValueError, KeyError, TypeError): pass
    records = []
    for j in db.conn.execute('SELECT * FROM jobs WHERE NOT EXISTS (SELECT 1 FROM lesson_deletions d WHERE d.job_id=jobs.id) ORDER BY id DESC'):
        if groups is not None and j['grp'] not in groups: continue
        publication = db.publication(j['id'], 'youtube')
        privacy = db.stage(j['id'], 'privacy')
        if not publication or not privacy or privacy['status'] != 'done': continue
        if str(publication['external_id'] or '').startswith('dry'): continue
        vid = video_id(publication['url'])
        if not vid: continue
        e = {**old.get(str(j['id']), {}), **(db.stage_output(j['id'], 'library_add') or {}).get('entry', {})}
        m = db.stage_output(j['id'], 'metadata') or {}
        seg = db.stage_output(j['id'], 'segments') or {}
        yt = db.stage_output(j['id'], 'youtube_upload') or {}
        chapters = e.get('chapters', m.get('chapters', [])) or []
        clean = []
        for c in chapters:
            t = c.get('final_t', c.get('start', c.get('t', c.get('time', 0))))
            if isinstance(t, (int, float)) and math.isfinite(t) and t >= 0:
                clean.append({'title': str(c.get('title', c.get('label', ''))), 'start': int(t),
                              'url': f'https://youtu.be/{vid}?t={int(t)}'})
        records.append({'job_id': j['id'], 'group': j['grp'], 'subject': e.get('subject') or j['subject'],
                        'title': e.get('title') or m.get('title') or _t('ui.app.lesson'),
                        'lesson_no': e.get('lesson_no', yt.get('lesson_no')),
                        'duration': e.get('duration', seg.get('final_sec')),
                        'published_at': e.get('published_at') or publication['created_at'],
                        'lesson_date': job_date(j), 'lesson_date_inferred': not bool(j['lesson_date']),
                        'url': f'https://youtu.be/{vid}', 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
                        'chapters': clean})
    return sorted(records, key=lambda e: (e['lesson_date'] or '', e['job_id']), reverse=True)


def statistics(db, assumptions=None, *, groups=None):
    counts = Counter(); publications = Counter(); breakdown = defaultdict(lambda: {'lessons':0, 'source_sec':0})
    source = published = cut = process = timed_source = completed_source = 0.; timed = 0; blurred = tracks = 0; blur_known = track_known = 0
    rates = defaultdict(list); errors = []; days = Counter(); inferred_dates = 0
    for j in db.conn.execute('SELECT * FROM jobs'):
        if groups is not None and j['grp'] not in groups: continue
        jid = j['id']; counts[j['status']] += 1
        probe = db.stage_output(jid, 'probe') or {}; seg = db.stage_output(jid, 'segments') or {}
        dur = probe.get('duration_sec') or seg.get('original_sec') or 0
        source += dur
        if j['status'] == 'done': completed_source += dur
        if seg.get('original_sec') is not None and seg.get('final_sec') is not None:
            cut += max(0, seg['original_sec']-seg['final_sec'])
        b = db.stage_output(jid, 'blur') or {}
        if isinstance(b.get('blurred_boxes'), (int,float)):
            blurred += b['blurred_boxes']; blur_known += 1
        if isinstance(b.get('student_tracks'), (int,float)):
            tracks += b['student_tracks']; track_known += 1
        elapsed = 0
        for st in db.stages(jid):
            dt = seconds(st['started_at'], st['finished_at']) if st['status'] == 'done' else 0
            elapsed += dt
            if dt: rates[st['name']].append(dt)
        if j['status'] == 'done' and elapsed:
            process += elapsed; timed_source += dur; timed += 1
        error = db.insights(jid).get('progress', {}).get('eta_error_sec')
        if error is not None: errors.append(error)
        for p in db.conn.execute('SELECT * FROM publications WHERE job_id=?', (jid,)):
            if p['external_id'] and not str(p['external_id']).startswith('dry'):
                publications[p['target']] += 1
                if p['target'] == 'youtube':
                    published += seg.get('final_sec') or 0
                    day = job_date(j)
                    if day: days[day] += 1
                    if not j['lesson_date']: inferred_dates += 1
        key = (j['grp'], j['subject'] or '—'); breakdown[key]['lessons'] += 1; breakdown[key]['source_sec'] += dur
    from reel.demo import read_demo_lessons
    demos=read_demo_lessons(db,groups)
    assumptions = assumptions or SAVINGS
    from reel.lesson_dates import today as lesson_today
    today = lesson_today()
    return {'demo_count':len(demos),'demo_source_sec':sum(r['duration_sec'] for r in demos),'counts': dict(counts), 'source_sec': source, 'published_sec': published, 'cut_sec': cut,
            'cut_ratio': cut/source if source else None, 'blurred_boxes': blurred if blur_known else None,
            'blur_unit': _t('text.insights.face_blurs_across_frames_the_same_face'),
            'student_tracks': tracks if track_known else None,
            'avg_processing_sec': process/timed if timed else None,
            'processing_ratio': process/timed_source if timed_source and timed else None,
            'eta_mean_error_sec': sum(errors)/len(errors) if errors else None, 'eta_samples': len(errors),
            'slow_stages': sorted([{'stage':k, 'seconds':sum(v)/len(v)} for k,v in rates.items()], key=lambda x:-x['seconds']),
            'publications': dict(publications), 'assumptions': assumptions,
            'completed_source_sec': completed_source,
            'saved_minutes': completed_source/3600*assumptions['editing_min_per_hour']+counts['done']*assumptions['publishing_min_per_lesson'],
            'groups': [{'group':g,'subject':s,**v} for (g,s),v in breakdown.items()],
            'date_basis': 'lesson_date', 'inferred_lesson_dates': inferred_dates,
            'daily': [{'date':str(today-timedelta(days=i)), 'count':days[str(today-timedelta(days=i))]} for i in range(29,-1,-1)]}
