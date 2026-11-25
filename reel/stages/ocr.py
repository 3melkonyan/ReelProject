"""Local Armenian + English board/slide OCR, FINAL blurred-video timestamps.

Tesseract is an optional executable dependency, never installed/downloaded at runtime.
Requires both eng.traineddata and hye.traineddata (tessdata_fast, Apache-2.0).
"""
from __future__ import annotations

from reel.i18n import t as _t
import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import cv2
import numpy as np


def recognize(image,minimum=45):
    if not shutil.which('tesseract'):
        try:
            import tesserocr
            from PIL import Image
        except ImportError:
            raise RuntimeError(_t('text.ocr.tesseract_hye_eng_is_not_installed')) from None
        # Wheel bundles the same native engine. Models come from TESSDATA_PREFIX,
        # set by the operator; runtime never downloads them.
        with tesserocr.PyTessBaseAPI(lang='hye+eng',psm=6) as api:
            api.SetImage(Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)))
            text=api.GetUTF8Text()
            return text if api.MeanTextConf()>=minimum else ''
    with tempfile.TemporaryDirectory(prefix='reel-ocr-') as tmp:
        path=Path(tmp)/'board.png';cv2.imwrite(str(path),image)
        run=subprocess.run(['tesseract',str(path),'stdout','-l','hye+eng','--psm','6','tsv'],
                           capture_output=True,text=True,check=True,timeout=45)
    lines={}
    # OCR glyphs are literal TSV fields, never CSV-quoted strings.
    for row in csv.DictReader(io.StringIO(run.stdout),delimiter='\t',quoting=csv.QUOTE_NONE):
        try:confidence=float(row.get('conf',-1))
        except (TypeError,ValueError):continue
        if row.get('level')!='5' or not minimum<=confidence<=100:continue
        text=row.get('text','').strip()
        if text:lines.setdefault((row['block_num'],row['par_num'],row['line_num']),[]).append(text)
    return '\n'.join(' '.join(words) for words in lines.values())


def crop_target(frame,plan,source_t):
    from reel.framing import matrix_at
    if not plan or plan.get('version')!=2:return None,None
    row=next((r for r in plan.get('timeline',[]) if r['start']<=source_t<r['end']),None)
    if not row or row.get('mode') not in ('board','projector','both') or row.get('corners') is None:return None,None
    quad=np.asarray(row['corners'],np.float32)*np.asarray(plan['size'])
    quad=cv2.perspectiveTransform(quad.astype(np.float32)[None],np.asarray(matrix_at(plan,source_t),float))[0]
    # Render output may be probed after encoder rounding.
    quad*=np.asarray([frame.shape[1],frame.shape[0]])/np.asarray(plan['output_size'])
    w=max(2,round(max(np.linalg.norm(quad[1]-quad[0]),np.linalg.norm(quad[2]-quad[3]))))
    h=max(2,round(max(np.linalg.norm(quad[3]-quad[0]),np.linalg.norm(quad[2]-quad[1]))))
    scale=min(1.,1600/w,900/h);w=max(2,round(w*scale));h=max(2,round(h*scale))
    matrix=cv2.getPerspectiveTransform(quad.astype(np.float32),np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]]))
    return cv2.warpPerspective(frame,matrix,(w,h)),('projector' if row['mode'] in ('projector','both') else 'board')


def extract(video,plan,options,*,time_map=None,recognizer=None):
    from reel.stages.processing import to_source
    read=recognizer or recognize;cap=cv2.VideoCapture(str(video));records=[];chapters=[];previous=None;source_before=None
    duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/max(1,cap.get(cv2.CAP_PROP_FPS))
    step=min(options['interval_sec'],options['slide_interval_sec']);last_board=-1e9
    try:
        for t in np.arange(0,duration,step):
            source_t=to_source(t,time_map) if time_map else t
            regime=next((r for r in (plan or {}).get('timeline',[]) if r['start']<=source_t<r['end']),{})
            if regime.get('mode')=='board':
                if t-last_board<options['interval_sec']:continue
                last_board=t
            elif regime.get('mode') not in ('projector','both'):continue
            cap.set(cv2.CAP_PROP_POS_MSEC,float(t)*1000);ok,frame=cap.read()
            if not ok:continue
            crop,source=crop_target(frame,plan,to_source(t,time_map) if time_map else t)
            if crop is None:previous=None;source_before=None;continue
            small=cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(160,90))
            changed=previous is None or source!=source_before or float(cv2.absdiff(previous,small).mean())>=options['change_threshold']
            # Board writing grows gradually: periodic OCR, slides only on change.
            interval=options['slide_interval_sec'] if source=='projector' else options['interval_sec']
            if not changed and source=='projector':
                if records and records[-1]['source']==source:records[-1]['t_end']=min(duration,t+interval)
                continue
            text=read(crop,options['min_confidence']).strip()
            previous=small;source_before=source
            if not text:continue
            if source=='board' and records and records[-1]['text']==text and records[-1]['source']==source:
                records[-1]['t_end']=min(duration,t+interval);continue
            records.append(dict(t_start=float(t),t_end=float(min(duration,t+interval)),source=source,text=text))
            if source=='projector' and changed:
                title=re.sub(r'\s+',' ',text.splitlines()[0]).strip()[:100]
                chapters.append(dict(final_t=float(t),title=title,source='ocr'))
    finally:cap.release()
    return {'segments':records,'chapters':chapters,'engine':'tesseract:hye+eng','sample_sec':options['interval_sec'],'slide_sample_sec':options['slide_interval_sec']}


def save(conn,job_id,rows):
    """Only called by an enabled processing stage; tests use isolated SQLite.

    Search consumers can SELECT by job_id, final t_start/t_end.
    """
    conn.execute('CREATE TABLE IF NOT EXISTS ocr_segments (job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, t_start REAL NOT NULL, t_end REAL NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS ocr_segments_job_time ON ocr_segments(job_id,t_start)')
    conn.execute('SAVEPOINT reel_ocr')
    try:
        conn.execute('DELETE FROM ocr_segments WHERE job_id=?',(job_id,))
        for r in rows:
            if not 0<=r['t_start']<r['t_end'] or r['source'] not in ('board','projector'):raise ValueError('Invalid OCR segment')
            conn.execute('INSERT INTO ocr_segments VALUES (?,?,?,?,?)',(job_id,r['t_start'],r['t_end'],r['source'],r['text']))
        conn.execute('RELEASE reel_ocr')
    except Exception:
        conn.execute('ROLLBACK TO reel_ocr');conn.execute('RELEASE reel_ocr');raise


def add_chapters(meta,chapters,rows=None):
    from reel.stages.processing import to_source
    from reel.stages.segments import fmt
    old=meta['chapters'];all_chapters=list(old)
    for c in chapters:
        # Slide change wins over an ASR chapter at the same second.
        all_chapters=[x for x in all_chapters if abs(x['final_t']-c['final_t'])>=1]
        all_chapters.append({**c,'t':to_source(c['final_t'],rows) if rows else c['final_t']})
    all_chapters.sort(key=lambda x:x['final_t'])
    block='\n'.join(f"{fmt(c['final_t'])} {c['title']}" for c in old)
    new='\n'.join(f"{fmt(c['final_t'])} {c['title']}" for c in all_chapters)
    meta={**meta,'chapters':all_chapters}
    if block:meta['youtube_description']=meta['youtube_description'].replace(block,new,1)[:5000]
    return meta
