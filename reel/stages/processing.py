"""Opt-in per-group processing. Defaults deliberately preserve the established pipeline.

All temporal edits expose a source/final time map; downstream subtitle/search consumers
must use it instead of summing keep intervals when smart pauses are enabled.
"""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path

DEFAULTS = {
    "thumbnail": {"enabled": False, "variant": 0},
    "studio_audio": {"enabled": False, "noise_reduction": 6., "tail_threshold": .008},
    "board_enhance": {"enabled": False, "amount": .18},
    "smart_pauses": {"enabled": False, "min_sec": 3., "max_sec": 10., "retain_sec": 1.,
                     "writing_speed": 1.75, "change_threshold": 2.5, "fade_sec": .08},
    "ocr": {"enabled": False, "interval_sec": 10., "slide_interval_sec": 1., "change_threshold": 8., "min_confidence": 45.},
}
BOUNDS = {"variant": (0,2), "noise_reduction": (0,12), "tail_threshold": (.001,.02),
          "amount": (0,.4), "min_sec": (3,10), "max_sec": (3,10), "retain_sec": (.8,2),
          "writing_speed": (1.5,2), "change_threshold": (.1,50), "fade_sec": (.02,.2),
          "interval_sec": (5,60), "slide_interval_sec": (.5,5), "min_confidence": (0,100)}


def settings(value=None):
    result=copy.deepcopy(DEFAULTS)
    if value is None:return result
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError("Unknown processing feature")
    for feature,options in value.items():
        if not isinstance(options,dict) or set(options)-set(DEFAULTS[feature]):raise ValueError("Unknown processing option")
        for key,v in options.items():
            if key=="enabled":
                if not isinstance(v,bool):raise ValueError("enabled must be boolean")
            else:
                lo,hi=BOUNDS[key]
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
                    raise ValueError(f"processing.{feature}.{key} outside {lo}..{hi}")
                if key=="variant" and int(v)!=v:raise ValueError("variant must be an integer")
            result[feature][key]=v
    p=result['smart_pauses']
    if p['min_sec']>p['max_sec']:raise ValueError("min_sec exceeds max_sec")
    return result


def for_ctx(ctx):
    return settings(ctx.config['groups'][ctx.group].get('processing'))


def studio_filter(options):
    if not options['enabled']:return ''
    # A gentle expander attenuates quiet room tails; this is NOT blind room-IR removal.
    return (f"highpass=f=70,lowpass=f=15000,afftdn=nr={options['noise_reduction']}:nf=-45:tn=1,"
            f"agate=threshold={options['tail_threshold']}:ratio=1.2:range=0.8:attack=20:release=220,")


def make_map(keep,events=(),fade=.08):
    """Piecewise source -> output map. Crossfades only at removed-silence joins.

    Overlap has two source images: the inverse chooses the incoming side. Speech
    and OCR never use overlapping boundaries (they are inside detected silence).
    """
    chunks=[];cursor=0.
    for a,b in keep:
        selected=sorted((e for e in events if a<=e['start']<e['end']<=b),key=lambda e:e['start'])
        pos=a
        parts=[]
        for e in selected:
            if pos<e['start']:parts.append((pos,e['start'],1.,False))
            if e['operation']=='speed':parts.append((e['start'],e['end'],e['speed'],False))
            else:
                retained=e['retain_sec'];half=retained/2
                parts.extend([(e['start'],e['start']+half,1.,False),
                              (e['end']-half,e['end'],1.,True)])
            pos=e['end']
        if pos<b:parts.append((pos,b,1.,False))
        for start,end,speed,join in parts:
            overlap=min(fade,(end-start)/speed/4,(chunks[-1]['final_end']-chunks[-1]['final_start'])/4) if join and chunks else 0.
            cursor-=overlap
            row=dict(source_start=start,source_end=end,final_start=cursor,
                     final_end=cursor+(end-start)/speed,speed=speed,fade=overlap)
            chunks.append(row);cursor=row['final_end']
    return chunks


def to_final(t,rows):
    for r in rows:
        if r['source_start']<=t<=r['source_end']:
            return r['final_start']+(t-r['source_start'])/r['speed']
    return None


def to_source(t,rows):
    for r in reversed(rows):
        if r['final_start']<=t<=r['final_end']:
            return r['source_start']+(t-r['final_start'])*r['speed']
    return rows[-1]['source_end'] if rows else t


def retime_graph(rows,video=False,input_index=0,rate=30):
    """One decode, split, retime, concat/crossfade. Same map for video and audio."""
    typ='v' if video else 'a';split='split' if video else 'asplit';g=[]
    g.append(f'[{input_index}:{typ}]{split}={len(rows)}'+''.join(f'[p{i}]' for i in range(len(rows))))
    for i,r in enumerate(rows):
        trim='trim' if video else 'atrim';reset='setpts' if video else 'asetpts'
        chain=f"[p{i}]{trim}=start={r['source_start']:.6f}:end={r['source_end']:.6f},{reset}=PTS-STARTPTS"
        chain+=f",setpts=PTS/{r['speed']:.6f},fps={rate}:start_time=0,settb=AVTB" if video else f",atempo={r['speed']:.6f}"
        g.append(chain+f'[c{i}]')
    previous='c0'
    for i in range(1,len(rows)):
        output=f'j{i}';r=rows[i]
        if r['fade']:
            filt=(f"xfade=transition=fade:duration={r['fade']:.6f}:offset={r['final_start']:.6f}" if video
                  else f"acrossfade=d={r['fade']:.6f}:c1=tri:c2=tri")
        else:filt=f"concat=n=2:v={int(video)}:a={int(not video)}"
        g.append(f'[{previous}][c{i}]{filt}'+(f",setpts=N/({rate}*TB),fps={rate},tpad=stop_mode=clone:stop_duration=0.2,trim=duration={r['final_end']:.6f},settb=AVTB" if video else '')+f'[{output}]');previous=output
    g.append(f'[{previous}]'+('null' if video else 'anull')+f'[{typ}]')
    return ';'.join(g)


def board_motion(video,start,end,framing):
    """Conservative: absent target/failed sampling means writing, never deletion."""
    import cv2
    import numpy as np
    from reel.stages.board_enhance import target_quad
    cap=cv2.VideoCapture(str(video));last=None;changes=[]
    try:
        for t in (start+.2,(start+end)/2,end-.2):
            cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
            if not ok:return None
            quad=target_quad(framing,t,frame.shape[1],frame.shape[0])
            if quad is None:return None
            small=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),(160,90))
            mask=np.zeros((90,160),np.uint8)
            cv2.fillConvexPoly(mask,(quad*[160/frame.shape[1],90/frame.shape[0]]).astype(np.int32),255)
            if last is not None:changes.append(float(np.percentile(cv2.absdiff(last,small)[mask>0],99.5)))
            last=small
    finally:cap.release()
    return max(changes) if changes else None


def pauses(result,video,audio,transcript,framing,options,max_ratio):
    from reel.stages.segments import detect_silences
    r=copy.deepcopy(result)
    if r.get('auto_note'):return r # preserve full-lesson fallback
    rows=transcript.get('segments',[]) if isinstance(transcript,dict) else transcript or []
    events=[];keep=r['keep'];total=r['original_sec']
    for start,end in detect_silences(audio,-40,options['min_sec']):
        if not options['min_sec']<=end-start<=options['max_sec']:continue
        start+=.2;end-=.2
        if not any(a<=start<end<=b for a,b in keep):continue
        if any(float(s['start'])<end and float(s['end'])>start and str(s.get('text','')).strip() for s in rows):continue
        change=board_motion(video,start,end,framing)
        # Unknown board activity is retained at original speed, not guessed.
        if change is None:continue
        writing=change>=options['change_threshold']
        event=dict(start=start,end=end,operation='speed' if writing else 'pause',
                   speed=options['writing_speed'] if writing else 1.,retain_sec=options['retain_sec'],
                   why='Լուռ գրել՝ արագացում' if writing else 'Լռության կրճատում',kind='other')
        candidate=make_map(keep,events+[event],options['fade_sec'])
        ratio=1-candidate[-1]['final_end']/total
        if ratio<=max_ratio:events.append(event)
    if events:
        mapping=make_map(keep,events,options['fade_sec'])
        r.update(time_map=mapping,pause_events=events,final_sec=mapping[-1]['final_end'],
                 cut_ratio=1-mapping[-1]['final_end']/total,
                 no_cut_code=None,no_cut_details={})
    return r


def remap_transcript(path,rows,out):
    data=json.loads(Path(path).read_text());segments=data.get('segments',[]) if isinstance(data,dict) else data
    mapped=[]
    for s in segments:
        a=to_final(float(s['start']),rows);b=to_final(float(s['end']),rows)
        if a is None or b is None or b<=a:continue
        entry={**s,'start':a,'end':b}
        if 'words' in entry:
            entry['words']=[{**w,'start':to_final(w['start'],rows),'end':to_final(w['end'],rows)}
                            for w in entry['words'] if to_final(w['start'],rows) is not None and to_final(w['end'],rows) is not None]
        mapped.append(entry)
    if isinstance(data,dict):data['segments']=mapped
    else:data=mapped
    Path(out).write_text(json.dumps(data,ensure_ascii=False))
    return Path(out)
