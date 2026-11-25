"""G-42 source-bound geometry; synthetic data only."""
import numpy as np
import pytest
from reel import framing as f


def test_target_rejects_full_frame_edges_and_union():
    assert not f.valid_target([[0,0],[1,0],[1,1],[0,1]])
    assert not f.valid_target([[.005,.2],[.8,.2],[.8,.8],[.005,.8]])
    assert f._union_target([[[.01,.01],[.99,.01],[.99,.99],[.01,.99]]]) is None


@pytest.mark.parametrize('correction',['rotate','perspective'])
def test_random_edge_crops_and_transitions_stay_inside(correction):
    rng=np.random.default_rng(42);rows=[]
    w,h=3840,2160
    for i in range(300):
        centre=rng.uniform([.03,.03],[.97,.97]);extent=rng.uniform(.05,.35,2)
        q=np.array([[-1,-1],[1,-1],[1,1],[-1,1]])*extent+centre
        angle=np.deg2rad(rng.uniform(0,5));r=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        q=(q-centre)@r.T+centre
        rows.append(dict(start=i*2,end=i*2+2,mode='board',epoch=0,corners=q.tolist(),confidence=.9))
    analysis=dict(timeline=rows,confidence=.9,samples=300,camera_changes=[],projector_tracks=0)
    plan=f.make_plan(analysis,w,h,correction=correction)
    output=[[0,0],[1920,0],[1920,1080],[0,1080]]
    for row in plan['timeline']:
        assert row['zoom']>=1-1e-6
    for t in np.arange(0,600,.25):
        source=f._project(output,np.linalg.inv(f.matrix_at(plan,t)))
        assert np.isfinite(source).all()
        assert (source>=-.5).all() and (source<=[w+.5,h+.5]).all()


def test_none_is_full_source():
    result=f.fit(None,3840,2160)
    assert result['zoom']==1
    np.testing.assert_allclose(result['source_quad'],[[0,0],[3840,0],[3840,2160],[0,2160]])


def test_three_adjacent_panels_and_outer_frame_preference():
    panels=[dict(q=np.array([[a,.2],[a+.2,.2],[a+.2,.7],[a,.7]]),area=.1,score=.8) for a in [.15,.36,.57]]
    joined=f._panels(panels)
    merged=max(joined,key=lambda x:x['area'])
    np.testing.assert_allclose(merged['q'],[[.15,.2],[.77,.2],[.77,.7],[.15,.7]])
    outer=dict(q=np.array([[.13,.18],[.79,.18],[.79,.72],[.13,.72]]),area=.3564,score=.8)
    assert max(f._panels(panels+[outer]),key=lambda x:x['score']) is outer


def test_projector_requires_brightness_three_spread_changes_and_stability():
    q=np.array([[.1,.2],[.9,.2],[.9,.8],[.1,.8]])
    tr=dict(events=[],quads=[q]*5,light=[True]*5)
    assert not f.projector_evidence(tr)
    tr['events']=[10,70,160]
    assert f.projector_evidence(tr)
    tr['light']=[False]*5
    assert not f.projector_evidence(tr)
    tr.update(light=[True]*5,events=[10,20,30])
    assert not f.projector_evidence(tr)


def test_forty_second_excursion_is_absorbed():
    q=[[.1,.2],[.9,.2],[.9,.8],[.1,.8]]
    rows=[dict(t=t,epoch=0,mode='none' if 100<=t<140 else 'board',targets=[] if 100<=t<140 else [q],confidence=.9) for t in range(0,240,10)]
    timeline=f.stabilize(rows,240)
    assert len(timeline)==1 and timeline[0]['mode']=='board'


@pytest.mark.parametrize('angle,expected',[(.6,0.),(2.,2.)])
def test_one_rotation_per_epoch(angle,expected):
    q=np.array([[.2,.3],[.8,.3],[.8,.65],[.2,.65]])*[3840,2160]
    import cv2
    q=cv2.transform(q.astype(np.float32)[None],cv2.getRotationMatrix2D((1920,1080),-angle,1))[0]/[3840,2160]
    rows=[dict(start=t,end=t+60,mode='board',epoch=0,corners=q.tolist(),confidence=.9) for t in [0,60]]
    p=f.make_plan(dict(timeline=rows,confidence=.9,samples=2,camera_changes=[],projector_tracks=0),3840,2160)
    assert len({round(r['rotation_deg'],3) for r in p['timeline']})==1
    assert abs(p['timeline'][0]['rotation_deg']-expected)<.01


def test_bright_surface_needs_real_spread_slide_changes(monkeypatch):
    q=np.array([[.2,.2],[.8,.2],[.8,.8],[.2,.8]],np.float32)
    def detected(image):
        if image is None:return []
        return [dict(q=q.copy(),score=.95,area=.36,brightness=240.,thumb=np.zeros((48,90),np.uint8))]
    monkeypatch.setattr(f,'candidates',detected)
    def image(slide):
        a=np.full((360,640,3),30,np.uint8)
        yy,xx=np.indices((216,384));v=np.where(((xx//24+yy//24+slide)%2)==0,160,240).astype(np.uint8)
        a[72:288,128:512]=v[:,:,None]
        return a
    still=f.analyze_samples([(t,image(0)) for t in range(0,190,10)],190)
    assert still['projector_tracks']==0
    moving=f.analyze_samples([(t,image(int(t>=30)+int(t>=90)+int(t>=150))) for t in range(0,190,10)],190)
    assert moving['projector_tracks']==1
    assert {r['mode'] for r in moving['timeline']}=={'projector'}


def test_shared_panel_frame_is_not_lost_to_separate_dark_contours():
    import cv2
    image=np.full((360,640,3),220,np.uint8)
    for left in (90,250,410):
        cv2.rectangle(image,(left,90),(left+145,280),(40,45,40),-1)
        cv2.rectangle(image,(left,90),(left+145,280),(130,130,130),3)
    found=f.candidates(image)
    target=np.array([[90,90],[555,90],[555,280],[90,280]])/[640,360]
    assert any(f.overlap(c['q'],target)>.9 for c in found)


def test_plane_warp_does_not_introduce_black_edge_pixels():
    from reel.framing_render import PlaneWarp
    w,h=1920,1080
    rows=[dict(start=0,end=2,mode='board',epoch=0,corners=[[.015,.02],[.98,.025],[.98,.98],[.015,.975]],confidence=.9)]
    p=f.make_plan(dict(timeline=rows,confidence=.9,samples=1,camera_changes=[],projector_tracks=0),w,h)
    buffer=bytes([120])*(w*h)+bytes([128])*(w*h//2)
    output=PlaneWarp(p).apply(buffer,w,h,0.)
    assert np.frombuffer(output,np.uint8,count=w*h).min()==120


def test_capped_opposite_tilt_transition_does_not_freeze_at_endpoint():
    import cv2
    rows=[];w,h=1920,1080
    q=np.float32([[600,400],[1320,400],[1320,680],[600,680]])
    for start,angle in [(0,-14),(30,14)]:
        rotated=cv2.transform(q[None],cv2.getRotationMatrix2D((w/2,h/2),-angle,1))[0]/[w,h]
        rows.append(dict(start=start,end=start+30,mode='board',epoch=start,corners=rotated.tolist(),confidence=.9))
    p=f.make_plan(dict(timeline=rows,confidence=.9,samples=2,camera_changes=[],projector_tracks=0),w,h)
    middle=f.matrix_at(p,30.5)
    assert not np.allclose(middle,p['timeline'][0]['matrix'])
    assert not np.allclose(middle,p['timeline'][1]['matrix'])
    source=f._project([[0,0],[w,0],[w,h],[0,h]],np.linalg.inv(middle))
    assert (source>=-.5).all() and (source<=[w+.5,h+.5]).all()
    assert f.magnification(middle,source)<=1.5001
