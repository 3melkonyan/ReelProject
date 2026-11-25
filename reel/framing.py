"""Conservative temporal teaching-surface detection and fixed-per-regime transforms.

No face models and no network. All times/corners are in the original joined source.
The legacy chalk-board detector remains a candidate, not a colour assumption.
"""
from __future__ import annotations
from collections import deque
import itertools
import math
from pathlib import Path
import subprocess
import time

import cv2
import numpy as np

STEP = 10.0
DWELL = 60.0
TRANSITION = 1.0
CONFIDENCE = .66
MAX_UPSCALE = 1.5
ANALYSIS_WIDTH = 640


def order(points):
    points=np.asarray(points,np.float32).reshape(4,2)
    centre=points.mean(0);angles=np.arctan2(points[:,1]-centre[1],points[:,0]-centre[0])
    points=points[np.argsort(angles)]
    return np.roll(points,-int(np.argmin(points.sum(1))),axis=0)


def area(q):return abs(float(cv2.contourArea(np.asarray(q,np.float32))))


def overlap(a,b):
    try:
        intersection,_=cv2.intersectConvexConvex(order(a),order(b))
        return max(0,float(intersection))/max(1e-9,area(a)+area(b)-intersection)
    except cv2.error:return 0.


def _thumb(image,q,size=(96,54)):
    w,h=size
    matrix=cv2.getPerspectiveTransform(order(q).astype(np.float32),np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]]))
    out=cv2.warpPerspective(image,matrix,size)
    return cv2.cvtColor(out,cv2.COLOR_BGR2GRAY)[3:-3,3:-3]


def valid_target(q, width=1., height=1.):
    q=np.asarray(q,float)
    return bool(q.shape==(4,2) and np.isfinite(q).all() and
                .065<=area(q)/(width*height)<=.85 and
                (q>=[.01*width,.01*height]).all() and
                (q<=[.99*width,.99*height]).all())


def _light(image,q):
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);h,w=gray.shape
    mask=np.zeros((h,w),np.uint8);cv2.fillConvexPoly(mask,np.int32(q),1)
    inside=cv2.erode(mask,np.ones((5,5),np.uint8)).astype(bool)
    ring=(cv2.dilate(mask,np.ones((21,21),np.uint8))>0)&(mask==0)
    return (float(np.median(gray[inside])) if inside.any() else 0.,
            float(np.median(gray[ring])) if ring.any() else 255.)


def _panels(found):
    """Join aligned adjacent panels, preferring an observed enclosing frame."""
    ordered=sorted(found,key=lambda x:np.asarray(x['q'])[:,0].mean());groups=[]
    for item in ordered:
        q=np.asarray(item['q']);placed=False
        for group in groups:
            a=np.asarray(group[-1]['q']);ha=(a[2:,1].mean()-a[:2,1].mean());hb=q[2:,1].mean()-q[:2,1].mean()
            gap=q[:,0].min()-a[:,0].max()
            if -.01<=gap<.03 and abs(a[:2,1].mean()-q[:2,1].mean())<.05 and abs(a[2:,1].mean()-q[2:,1].mean())<.05 and abs(ha-hb)<.15*max(ha,hb):
                group.append(item);placed=True;break
        if not placed:groups.append([item])
    result=list(found)
    for group in groups:
        if len(group)<2:continue
        left=np.asarray(group[0]['q']);right=np.asarray(group[-1]['q'])
        q=np.array([left[0],right[1],right[2],left[3]])
        if not valid_target(q):continue
        outer=[x for x in found if all(all(cv2.pointPolygonTest(np.float32(x['q']),tuple(map(float,p)),False)>=-.005 for p in np.asarray(g['q'])) for g in group)]
        if outer:
            chosen=min(outer,key=lambda x:x['area']);chosen['score']=min(1.,max(chosen['score'],max(g['score'] for g in group))+.04)
        else:result.append({**group[0],'q':q,'area':area(q),'score':min(1.,max(g['score'] for g in group)+.04)})
    return result


def candidates(image):
    """Several independent masks/edge contours; works on white, dark and coloured surfaces."""
    if image is None:return []
    h,w=image.shape[:2];gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    gray=cv2.GaussianBlur(gray,(5,5),0);edges=cv2.Canny(gray,35,110)
    kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(5,5))
    masks=[cv2.morphologyEx(edges,cv2.MORPH_CLOSE,kernel)]
    for flag in (cv2.THRESH_BINARY,cv2.THRESH_BINARY_INV):
        _,mask=cv2.threshold(gray,0,255,flag+cv2.THRESH_OTSU)
        masks.append(cv2.morphologyEx(mask,cv2.MORPH_CLOSE,kernel))
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    masks.append(cv2.inRange(hsv,(25,35,20),(100,255,230)))
    quads=[]
    for mask in masks:
        contours,_=cv2.findContours(mask,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:60]:
            if cv2.contourArea(contour)<.055*w*h:continue
            hull=cv2.convexHull(contour);perimeter=cv2.arcLength(hull,True)
            for epsilon in (.015,.025,.045):
                polygon=cv2.approxPolyDP(hull,epsilon*perimeter,True)
                if len(polygon)==4 and cv2.isContourConvex(polygon):quads.append(order(polygon));break
    # A shared metal frame can split a three-panel dark board into six contours.
    # Recover only a broad, sustained measured occupancy band, never the wall or
    # a narrow rolled-screen rail. This is an additional candidate, not a mode.
    _,dark=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    occupied=(dark[:,max(1,int(.02*w)):int(.98*w)]>0).mean(axis=1)>.45
    boundaries=np.flatnonzero(np.diff(np.r_[False,occupied,False].astype(int)))
    for top,bottom in zip(boundaries[::2],boundaries[1::2]):
        if bottom-top<.2*h:continue
        columns=(dark[top:bottom]>0).mean(axis=0)>.55
        edges_x=np.flatnonzero(columns)
        if len(edges_x):quads.append(np.float32([[edges_x[0],top],[edges_x[-1],top],[edges_x[-1],bottom],[edges_x[0],bottom]]))
    # Keep the measured RANSAC case, including posts and lecturer legs.
    from reel.board import detect_frame
    old=detect_frame(gray)
    if old is not None:quads.append(order(old))
    found=[]
    for q in quads:
        fraction=area(q)/(w*h)
        if not valid_target(q,w,h):continue
        top,bottom=np.linalg.norm(q[1]-q[0]),np.linalg.norm(q[2]-q[3])
        left,right=np.linalg.norm(q[3]-q[0]),np.linalg.norm(q[2]-q[1])
        aspect=(top+bottom)/max(1,left+right)
        if not .8<=aspect<=6 or min(top,bottom,left,right)<min(w,h)*.12:continue
        if max(top,bottom)/max(1,min(top,bottom))>2.2 or max(left,right)/max(1,min(left,right))>2.2:continue
        angles=[]
        for i in range(4):
            a=q[(i-1)%4]-q[i];b=q[(i+1)%4]-q[i]
            angles.append(math.degrees(math.acos(float(np.clip(np.dot(a,b)/max(1e-6,np.linalg.norm(a)*np.linalg.norm(b)),-1,1)))))
        if min(angles)<45 or max(angles)>135:continue
        rectangularity=area(q)/max(1,np.prod(cv2.minAreaRect(q)[1]))
        region=np.zeros((h,w),np.uint8);cv2.fillConvexPoly(region,q.astype(np.int32),255)
        inside=cv2.erode(region,kernel)>0;outside=(cv2.dilate(region,kernel)>0)&(region==0)
        contrast=abs(float(np.median(gray[inside]))-float(np.median(gray[outside]))) if inside.any() and outside.any() else 0
        boundary=cv2.morphologyEx(region,cv2.MORPH_GRADIENT,kernel)>0
        edge_support=float((edges[boundary]>0).mean()) if boundary.any() else 0
        score=.23*min(1,fraction/.3)+.25*rectangularity+.22*(1-np.mean(np.abs(np.array(angles)-90))/90)+.15*min(1,contrast/45)+.15*min(1,edge_support/.18)
        thumb=_thumb(image,q)
        item={'q':q/[w,h],'score':float(score),'area':fraction,'brightness':float(np.percentile(thumb,95)),
              'thumb':thumb,'contrast':contrast}
        same=next((i for i,x in enumerate(found) if overlap(x['q'],item['q'])>.85),None)
        if same is None:found.append(item)
        elif score+fraction*.02>found[same]['score']+found[same]['area']*.02:found[same]=item
    return sorted(_panels(found),key=lambda x:x['score']+.1*x['area'],reverse=True)[:8]


def _motion(a,b,qa,qb):
    if a is None or b is None:return False
    if a.shape!=b.shape:return True
    x=cv2.resize(cv2.cvtColor(a,cv2.COLOR_BGR2GRAY),(160,90)).astype(np.float32)
    y=cv2.resize(cv2.cvtColor(b,cv2.COLOR_BGR2GRAY),(160,90)).astype(np.float32)
    shift,response=cv2.phaseCorrelate(cv2.Laplacian(x,cv2.CV_32F),cv2.Laplacian(y,cv2.CV_32F))
    moved=math.hypot(shift[0]/160,shift[1]/90)>.022
    # Slide/lecturer changes alone must not move the physical quadrilateral.
    geometry=bool(qa and qb and min(np.abs(i['q']-j['q']).max() for i in qa[:3] for j in qb[:3])>.022)
    best=max(((overlap(i['q'],j['q']),i,j) for i in qa for j in qb),key=lambda x:x[0],default=(0,None,None))
    coherent=bool(best[0]>.45 and np.abs(best[1]['q']-best[2]['q']).max()>.03)
    return bool((moved and response>.18 and geometry) or coherent)


def _median_samples(samples):
    # Five observations / 40 s remove moving occluders without buffering a lesson.
    # Geometry uses the median; screen activity uses the CURRENT image, not a delayed median.
    pending=deque();history=deque(maxlen=2);last=None;epoch=0
    def add(raw):
        nonlocal last,epoch
        t,image=raw;detected=candidates(image)
        moved=last is not None and _motion(last[1],image,last[2],detected)
        if moved:epoch+=1
        item=(t,image,detected,epoch,moved);last=item
        return item
    for raw in samples:
        pending.append(add(raw))
        if len(pending)<3:continue
        current=pending[0]
        neighbors=[x for x in [*history,*pending] if x[3]==current[3]]
        valid=[x[1] for x in neighbors if x[1] is not None]
        background=np.median(np.stack(valid),axis=0).astype(np.uint8) if current[1] is not None and valid else None
        yield current[0],background,[x[2] for x in neighbors],current[3],current[4],current[1]
        history.append(pending.popleft())
    while pending:
        current=pending[0];neighbors=[x for x in [*history,*pending] if x[3]==current[3]]
        valid=[x[1] for x in neighbors if x[1] is not None]
        background=np.median(np.stack(valid),axis=0).astype(np.uint8) if current[1] is not None and valid else None
        yield current[0],background,[x[2] for x in neighbors],current[3],current[4],current[1]
        history.append(pending.popleft())


def projector_evidence(track):
    events=track['events'];quads=track['quads']
    return bool(len(events)>=3 and events[-1]-events[0]>=120 and
                track['light'] and np.mean(track['light'])>=.6 and
                quads and min(overlap(quads[0],q) for q in quads)>.8)


def analyze_samples(samples,total,*,mode='auto',step=STEP,min_dwell=DWELL):
    """Whole-pass decisions; later slide evidence also labels the earlier life of a screen."""
    tracks=[];rows=[];camera=[]
    for t,image,neighbors,epoch,moved,current in _median_samples(iter(samples)):
        detected=candidates(image);items=[];used=set()
        for candidate in detected:
            q=candidate['q']
            thumb=_thumb(current,q*[current.shape[1],current.shape[0]])
            candidate['thumb']=thumb;candidate['brightness']=float(np.percentile(thumb,95))
            median,surround=_light(current,q*[current.shape[1],current.shape[0]])
            support=sum(any(overlap(q,x['q'])>.65 for x in collection) for collection in neighbors)/len(neighbors)
            confidence=.72*candidate['score']+.28*support
            choices=[(overlap(q,tr['q']),i) for i,tr in enumerate(tracks) if i not in used and t-tr['last']<120 and tr['epoch']==epoch]
            best=max(choices,default=(0,None));ident=best[1] if best[0]>.42 else None
            if ident is None:
                ident=len(tracks);tracks.append({'q':q,'last':t,'epoch':epoch,'thumb':None,'events':[],'quads':[],'light':[],'max_brightness':0.,'brightness':candidate['brightness']})
            tr=tracks[ident];thumb=candidate['thumb'];change=dense=balance=0.
            if tr['thumb'] is not None and not moved:
                diff=thumb.astype(float)-tr['thumb'].astype(float)
                changed=np.abs(diff)>20;change=float(changed.mean());positive=float((diff>20).mean());negative=float((diff< -20).mean())
                balance=min(positive,negative)/max(1e-6,positive+negative)
                tiles=cv2.resize(changed.astype(np.float32),(6,4),interpolation=cv2.INTER_AREA)
                dense=float((tiles>.05).mean())
                if change>.07 and balance>.18 and dense>.45:tr['events'].append(float(t))
            tr['quads'].append(q.copy());tr['light'].append(candidate['brightness']>=170 and median>=surround+40)
            tr.update(q=q,last=t,thumb=thumb,brightness=candidate['brightness'],max_brightness=max(tr['max_brightness'],candidate['brightness']))
            used.add(ident);items.append({'id':ident,'q':q.tolist(),'confidence':round(confidence,4),
                                       'brightness':candidate['brightness'],'activity':change,'area':candidate['area']})
        rows.append({'t':float(t),'epoch':epoch,'motion':moved,'items':items})
        if moved:camera.append({'from':rows[-2]['t'] if len(rows)>1 else t,'to':float(t)})
    projectors={i for i,tr in enumerate(tracks) if projector_evidence(tr)}
    # A projection inside a larger board must not turn the WHOLE board into a screen.
    for outer in list(projectors):
        if any(inner!=outer and area(tracks[inner]['q'])<.8*area(tracks[outer]['q']) and
               cv2.pointPolygonTest(np.float32(tracks[outer]['q']),tuple(map(float,np.mean(tracks[inner]['q'],0))),False)>=0
               for inner in projectors):projectors.discard(outer)
    for row in rows:
        eligible=[x for x in row['items'] if x['confidence']>=CONFIDENCE]
        screens=[x for x in eligible if x['id'] in projectors and x['brightness']>max(55,.65*tracks[x['id']]['max_brightness'])]
        boards=[x for x in eligible if x['id'] not in projectors]
        for board in boards:
            explained=sum(s['activity']*s['area']/max(.01,board['area']) for s in screens if overlap(s['q'],board['q'])>.05)
            board['activity']=max(0,board['activity']-explained)
        if mode=='projector':
            selected=screens
            selected=sorted(selected,key=lambda x:x['confidence']+.1*x['area'],reverse=True)[:1]
            state='projector' if selected else 'none'
        elif mode=='board':selected=sorted(boards or eligible,key=lambda x:x['confidence']+.15*x['area'],reverse=True)[:1];state='board' if selected else 'none'
        elif screens:
            active=[b for b in boards if b['activity']>.008]
            selected=screens[:1]+active[:1];state='both' if active else 'projector'
        else:
            selected=sorted(boards,key=lambda x:x['confidence']+.15*x['area'],reverse=True)[:1]
            state='board' if selected else 'none'
            # Two equally plausible, inactive rectangles are not a confident semantic choice.
            if len(boards)>1 and abs(boards[0]['confidence']-boards[1]['confidence'])<.025 and overlap(boards[0]['q'],boards[1]['q'])<.1:
                selected=[];state='none'
        row.update(mode=state,targets=[x['q'] for x in selected],confidence=min((x['confidence'] for x in selected),default=0.))
        row.pop('items')
    for interval in camera:
        for row in rows:
            if interval['from']<=row['t']<=interval['to']:row.update(mode='none',targets=[],confidence=0.)
    timeline=stabilize(rows,total,min_dwell=min_dwell)
    return {'timeline':timeline,'samples':len(rows),'camera_changes':camera,'projector_tracks':len(projectors),
            'uncertain_seconds':sum(x['end']-x['start'] for x in timeline if x['mode']=='none'),
            'confidence':round(float(np.mean([r['confidence'] for r in rows])),3) if rows else 0.,
            'observations':rows}


def _raw_union_target(targets):
    if not targets:return None
    arrays=[np.asarray(q,float) for q in targets]
    # Same-plane containment, e.g. a projector illuminating a board: preserve its true quad.
    largest=max(arrays,key=area)
    if all(overlap(largest,q)>.65 for q in arrays):
        median=np.median(np.stack(arrays),axis=0).astype(np.float32)
        level=cv2.getPerspectiveTransform(median,np.float32([[0,0],[1,0],[1,1],[0,1]]))
        projected=_project(np.concatenate(arrays),level);lo=projected.min(0);hi=projected.max(0)
        return _project([[lo[0],lo[1]],[hi[0],lo[1]],[hi[0],hi[1]],[lo[0],hi[1]]],np.linalg.inv(level)).tolist()
    if all(all(cv2.pointPolygonTest(np.float32(largest),tuple(map(float,p)),False)>=-1e-5 for p in q) for q in arrays):return largest.tolist()
    points=np.concatenate(arrays);lo=points.min(0);hi=points.max(0)
    return [[lo[0],lo[1]],[hi[0],lo[1]],[hi[0],hi[1]],[lo[0],hi[1]]]


def _union_target(targets):
    q=_raw_union_target(targets)
    return q if q is not None and valid_target(q) else None


def stabilize(rows,total,*,min_dwell=DWELL):
    runs=[]
    for i,row in enumerate(rows):
        start=0. if i==0 else row['t'];end=rows[i+1]['t'] if i+1<len(rows) else total
        if end<=start:continue
        if runs and runs[-1]['mode']==row['mode'] and runs[-1]['epoch']==row['epoch']:
            runs[-1]['end']=end;runs[-1]['targets']+=row['targets'];runs[-1]['scores'].append(row['confidence'])
        else:runs.append({'start':start,'end':end,'mode':row['mode'],'epoch':row['epoch'],'targets':list(row['targets']),'scores':[row['confidence']]})
    i=1
    while i<len(runs)-1:
        a,b,c=runs[i-1:i+2]
        qa=_union_target(a['targets']);qc=_union_target(c['targets'])
        moving=any(r.get('motion') and b['start']<=r['t']<b['end'] for r in rows)
        if b['end']-b['start']<min_dwell and not moving and a['epoch']==b['epoch']==c['epoch'] and a['mode']==c['mode'] and qa is not None and qc is not None and overlap(qa,qc)>.8:
            runs[i-1:i+2]=[{**a,'end':c['end'],'targets':a['targets']+c['targets'],'scores':a['scores']+c['scores']}]
            i=max(1,i-1)
        else:i+=1
    # Merge short excursions conservatively: full frame if ANY merged observation was unsure.
    while len(runs)>1:
        index=next((i for i,r in enumerate(runs) if r['end']-r['start']<min_dwell),None)
        if index is None:break
        short=runs[index]
        if short['mode']=='none':
            # Pad an uncertain/moving-camera interval to the dwell time, rather than
            # erasing a whole neighbouring, confidently detected minute.
            deficit=min_dwell-(short['end']-short['start'])
            a=max(0.,short['start']-deficit/2);b=min(total,a+min_dwell);a=max(0.,b-min_dwell)
            if index and a-runs[index-1]['start']<min_dwell:a=runs[index-1]['start']
            if index+1<len(runs) and runs[index+1]['end']-b<min_dwell:b=runs[index+1]['end']
            before=[dict(r,end=min(r['end'],a)) for r in runs[:index] if r['start']<a]
            after=[dict(r,start=max(r['start'],b)) for r in runs[index+1:] if r['end']>b]
            runs=before+[{**short,'start':a,'end':b}]+after
            continue
        neighbor=index-1 if index else 1;a,b=sorted((index,neighbor));left,right=runs[a],runs[b]
        state='none' if 'none' in (left['mode'],right['mode']) else left['mode'] if left['mode']==right['mode'] else 'none'
        runs[a:b+1]=[{'start':left['start'],'end':right['end'],'mode':state,'epoch':right['epoch'],
                       'targets':left['targets']+right['targets'],'scores':left['scores']+right['scores']}]
    joined=[]
    for run in runs:
        if joined and joined[-1]['mode']==run['mode'] and (run['mode']=='none' or joined[-1]['epoch']==run['epoch']):
            joined[-1]['end']=run['end'];joined[-1]['targets']+=run['targets'];joined[-1]['scores']+=run['scores']
        else:joined.append(run)
    result=[]
    for run in joined:
        targets=run.pop('targets');scores=run.pop('scores')
        # Stable crop contains all accepted target observations; no frame-to-frame tracking jitter.
        run['corners']=None if run['mode']=='none' else _union_target(targets)
        if run['corners'] is None:run['mode']='none'
        run['confidence']=round(float(np.median(scores)),3)
        result.append(run)
    if not result:result=[{'start':0.,'end':float(total),'mode':'none','epoch':0,'corners':None,'confidence':0.}]
    return result


def output_size(width,height):
    k=min(1.,width/1920,height/1080)
    return [max(2,int(1920*k)//2*2),max(2,int(1080*k)//2*2)]


def _project(points,matrix):
    p=np.c_[np.asarray(points,float),np.ones(len(points))]@np.asarray(matrix,float).T
    return p[:,:2]/p[:,2:]


def magnification(matrix,points):
    h=np.asarray(matrix,float);values=[]
    for x,y in np.asarray(points,float):
        den=h[2,0]*x+h[2,1]*y+h[2,2]
        if abs(den)<1e-7:return float('inf')
        u=h[0,0]*x+h[0,1]*y+h[0,2];v=h[1,0]*x+h[1,1]*y+h[1,2]
        jac=np.array([[(h[0,0]*den-u*h[2,0])/den**2,(h[0,1]*den-u*h[2,1])/den**2],
                      [(h[1,0]*den-v*h[2,0])/den**2,(h[1,1]*den-v*h[2,1])/den**2]])
        values.append(float(np.linalg.svd(jac,compute_uv=False)[0]))
    return max(values,default=0.)


def _unbounded_fit(corners,width,height,*,correction='rotate',size=None,margin=.02,max_upscale=MAX_UPSCALE,rotation_deg=None):
    """Smallest output-aspect rect enclosing the levelled target, widened for local scale cap."""
    size=size or output_size(width,height);ow,oh=size;aspect=ow/oh
    full=np.float32([[0,0],[width,0],[width,height],[0,height]])
    if corners is None:
        scale=min(ow/width,oh/height)
        h=np.array([[scale,0,(ow-width*scale)/2],[0,scale,(oh-height*scale)/2],[0,0,1.]])
        return {'matrix':h.tolist(),'source_quad':_project([[0,0],[ow,0],[ow,oh],[0,oh]],np.linalg.inv(h)).tolist(),'scale':scale,'correction':'none'}
    q=order(np.asarray(corners)*[width,height]).astype(np.float64)
    if not np.isfinite(q).all() or area(q)<1:raise ValueError('Invalid target quadrilateral')
    if correction=='perspective':
        tw=(np.linalg.norm(q[1]-q[0])+np.linalg.norm(q[2]-q[3]))/2
        th=(np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2
        base=cv2.getPerspectiveTransform(q.astype(np.float32),np.float32([[0,0],[tw,0],[tw,th],[0,th]]))
    elif correction=='rotate':
        from reel.board import tilt_deg
        base=np.vstack([cv2.getRotationMatrix2D((width/2,height/2),tilt_deg(q) if rotation_deg is None else rotation_deg,1.),[0,0,1]])
    else:raise ValueError('Unknown framing correction')
    level=_project(q,base);lo=level.min(0);hi=level.max(0);centre=(lo+hi)/2
    cw=max((hi[0]-lo[0])*(1+2*margin),(hi[1]-lo[1])*(1+2*margin)*aspect)
    grid=np.array([q[0]*(1-u)*(1-v)+q[1]*u*(1-v)+q[2]*u*v+q[3]*(1-u)*v for u in np.linspace(0,1,5) for v in np.linspace(0,1,5)])
    for _ in range(12):
        ch=cw/aspect;scale=ow/cw
        h=np.array([[scale,0,ow/2-centre[0]*scale],[0,scale,oh/2-centre[1]*scale],[0,0,1.]])@base
        source_quad=_project([[0,0],[ow,0],[ow,oh],[0,oh]],np.linalg.inv(h))
        output_grid=np.array([[x,y] for x in np.linspace(0,ow,5) for y in np.linspace(0,oh,5)])
        visible=_project(output_grid,np.linalg.inv(h))
        visible=visible[(visible[:,0]>=0)&(visible[:,0]<=width)&(visible[:,1]>=0)&(visible[:,1]<=height)]
        checks=np.r_[grid,visible]
        maximum=magnification(h,checks)
        if maximum<=max_upscale*(1+1e-6):break
        cw*=maximum/max_upscale*1.0001
    if maximum>max_upscale*1.001:raise ValueError('Scale cap could not be satisfied')
    if not np.isfinite(h).all() or not np.isfinite(source_quad).all():raise ValueError('Unstable homography')
    return {'matrix':h.tolist(),'source_quad':source_quad.tolist(),'scale':round(maximum,6),'correction':correction,
            'target_pixels':q.tolist(),'fit_rect':[float(centre[0]-cw/2),float(centre[1]-cw/aspect/2),float(cw),float(cw/aspect)]}


def fit(corners,width,height,*,correction='rotate',size=None,margin=.02,max_upscale=MAX_UPSCALE,rotation_deg=None):
    """Try margin, zero margin, zero rotation, then a contained axis-aligned crop.

    All inverse output corners stay inside the source. A full-source fallback
    stretches only for a non-output-aspect source; it never invents border pixels.
    """
    size=size or output_size(width,height);ow,oh=size
    full=np.float64([[0,0],[width,0],[width,height],[0,height]])
    def inside(q):return np.isfinite(q).all() and (q>=-1e-5).all() and (q<=[width+1e-5,height+1e-5]).all()
    if corners is not None:
        for pad,angle in [(margin,rotation_deg),(0.,rotation_deg),(0.,0.)]:
            result=_unbounded_fit(corners,width,height,correction=correction,size=size,margin=pad,
                                  max_upscale=max_upscale,rotation_deg=angle)
            if inside(np.array(result['source_quad'])):
                result['rotation_deg']=float(np.degrees(np.arctan2(-np.asarray(result['matrix'])[1,0],np.asarray(result['matrix'])[0,0]))) if correction=='rotate' else 0.
                result['zoom']=max(1.,float(width/max(1e-9,np.linalg.norm(np.array(result['source_quad'])[1]-result['source_quad'][0]))))
                return result
        q=np.asarray(corners,float)*[width,height]
        if not np.isfinite(q).all():raise ValueError('Invalid target quadrilateral')
        lo=q.min(0);hi=q.max(0);centre=(lo+hi)/2;aspect=ow/oh
        cw=min(width,height*aspect,max(hi[0]-lo[0],(hi[1]-lo[1])*aspect,ow/max_upscale))
        ch=cw/aspect;centre=np.clip(centre,[cw/2,ch/2],[width-cw/2,height-ch/2])
        full=np.array([centre+[-cw/2,-ch/2],centre+[cw/2,-ch/2],centre+[cw/2,ch/2],centre+[-cw/2,ch/2]])
    matrix=cv2.getPerspectiveTransform(full.astype(np.float32),np.float32([[0,0],[ow,0],[ow,oh],[0,oh]]))
    return {'matrix':matrix.tolist(),'source_quad':full.tolist(),'scale':magnification(matrix,full),
            'zoom':float(width/np.linalg.norm(full[1]-full[0])),'rotation_deg':0.,'correction':'none' if corners is None else correction}


def make_plan(analysis,width,height,*,correction='rotate'):
    size=output_size(width,height);timeline=[];uncertain=0.
    from reel.board import tilt_deg
    tilts={}
    for observed in analysis.get('observations',analysis['timeline']):
        targets=observed.get('targets') or ([observed['corners']] if observed.get('corners') is not None else [])
        if observed['mode']=='board' and observed.get('confidence',0)>=CONFIDENCE:
            tilts.setdefault(observed['epoch'],[]).extend(tilt_deg(np.asarray(q)*[width,height]) for q in targets)
    angles={}
    for epoch,values in tilts.items():
        median=float(np.median(values));mad=float(np.median(np.abs(np.asarray(values)-median)))
        angles[epoch]=median if abs(median)>1. and mad<.5 else 0.
    for row in analysis['timeline']:
        try:
            geometry=fit(row['corners'],width,height,correction=correction,size=size,rotation_deg=angles.get(row['epoch'],0.))
        except (ValueError, cv2.error, np.linalg.LinAlgError):
            row={**row,'mode':'none','corners':None,'confidence':0.,'fallback':'unstable geometry'}
            geometry=fit(None,width,height,size=size)
        if row['mode']=='none':uncertain+=row['end']-row['start']
        timeline.append({**row,**geometry})
    # If one crop cannot safely use the shared rotation, keep the whole epoch level
    # with zero correction rather than rotate differently within one camera view.
    reset={r['epoch'] for r in timeline if r['mode']!='none' and correction=='rotate' and abs(r['rotation_deg']-angles.get(r['epoch'],0.))>.01}
    for row in timeline:
        if row['epoch'] in reset and row['mode']!='none':
            row.update(fit(row['corners'],width,height,correction=correction,size=size,rotation_deg=0.))
    return {'version':2,'size':[width,height],'output_size':size,'correction':correction,'transition_sec':TRANSITION,
            'max_upscale':MAX_UPSCALE,'timeline':timeline,'confidence':analysis['confidence'],
            'uncertain_seconds':uncertain,'camera_changes':analysis['camera_changes'],
            'samples':analysis['samples'],'projector_tracks':analysis['projector_tracks']}


def matrix_at(plan,t):
    timeline=plan['timeline'];index=next((i for i,r in enumerate(timeline) if t<r['end']),len(timeline)-1)
    current=timeline[index];duration=plan.get('transition_sec',TRANSITION)
    if index and duration>0 and t<current['start']+duration:
        previous=timeline[index-1];u=max(0,min(1,(t-current['start'])/duration));u=u*u*(3-2*u)
        source=(1-u)*np.array(previous['source_quad'])+u*np.array(current['source_quad'])
        ow,oh=plan['output_size']
        matrix=cv2.getPerspectiveTransform(source.astype(np.float32),np.float32([[0,0],[ow,0],[ow,oh],[0,oh]]))
        # Convex interpolation of contained source corners cannot expose a border.
        # Opposite tilts can briefly magnify beyond both endpoints. Blend toward
        # the contained full-source quad just enough to retain the cap, preserving
        # a smooth transition instead of freezing/jumping to an endpoint.
        grid=np.array([[x,y] for x in np.linspace(0,ow,7) for y in np.linspace(0,oh,7)])
        cap=plan.get('max_upscale',MAX_UPSCALE)
        visible=_project(grid,np.linalg.inv(matrix))
        if magnification(matrix,visible)>cap*1.00001:
            width,height=plan['size'];full=np.array([[0,0],[width,0],[width,height],[0,height]],float)
            destination=np.float32([[0,0],[ow,0],[ow,oh],[0,oh]])
            low,high=0.,1.
            safe=cv2.getPerspectiveTransform(full.astype(np.float32),destination)
            for _ in range(20):
                blend=(low+high)/2;quad=(1-blend)*source+blend*full
                candidate=cv2.getPerspectiveTransform(quad.astype(np.float32),destination)
                visible=_project(grid,np.linalg.inv(candidate))
                if magnification(candidate,visible)<=cap:
                    high=blend;safe=candidate
                else:low=blend
            matrix=safe
        return matrix
    return np.asarray(current['matrix'],float)


def source_time(t,keep):
    for start,end in keep:
        if t<end-start:return start+t
        t-=end-start
    return keep[-1][1]


def sample_video(video,*,step=STEP,width=ANALYSIS_WIDTH):
    from reel.stages.transcribe import media_duration
    total=media_duration(video)
    for t in np.arange(0,max(.001,total),step):
        # Independent fast seeks avoid decoding discarded 10-second gaps.
        r=subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',f'{t:.3f}','-i',str(video),'-frames:v','1',
                          '-vf',f'scale={width}:-2','-pix_fmt','bgr24','-f','rawvideo','-'],capture_output=True,timeout=60)
        h=len(r.stdout)//(width*3)
        if r.returncode or not h:
            yield float(t),None
            continue
        yield float(t),np.frombuffer(r.stdout[:h*width*3],np.uint8).reshape(h,width,3)


def analyze_video(video,*,mode='auto',correction='rotate',step=STEP):
    from reel.stages.transcribe import media_duration
    from reel.stages.render import video_info
    started=time.perf_counter();info=video_info(video);total=media_duration(video)
    analysis=analyze_samples(sample_video(video,step=step),total,mode=mode,step=step)
    plan=make_plan(analysis,info['width'],info['height'],correction=correction)
    plan['analysis_seconds']=round(time.perf_counter()-started,3)
    return plan


def preview(image,plan,t=0):
    h,w=image.shape[:2];sw,sh=plan['size'];ow,oh=plan['output_size']
    matrix=matrix_at(plan,t)@np.diag([sw/w,sh/h,1.])
    return cv2.warpPerspective(image,matrix,(ow,oh),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
