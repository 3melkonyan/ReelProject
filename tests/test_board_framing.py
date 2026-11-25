"""Geometry, temporal classification and the real single-encode render path."""
import json,subprocess

import cv2
import numpy as np
import pytest
from reel import framing as f
from reel.config import validate_config
from framing_scene import surface,mixed,W,H


@pytest.mark.parametrize('kind',['white','black','green'])
@pytest.mark.parametrize('skew',[False,True])
def test_surface_candidates_on_different_coloured_walls(kind,skew):
    image,truth=surface(kind,skew=skew)
    candidates=f.candidates(image)
    assert candidates
    best=max(candidates,key=lambda c:f.overlap(c['q'],truth/[W,H]))
    assert f.overlap(best['q'],truth/[W,H])>.9


def test_temporal_median_removes_moving_lecturer():
    frames=[(i*10,surface(person=True,person_x=x)[0]) for i,x in enumerate([120,220,320,420,480])]
    result=f.analyze_samples(frames,50,mode='board')
    assert result['timeline'][0]['mode']=='board'
    assert result['confidence']>.7


def test_slides_are_projector_evidence_not_growing_board_writing():
    result=f.analyze_samples([(t,mixed(t)) for t in range(0,150,10)],150)
    modes=[r['mode'] for r in result['timeline']]
    # an 80-second activation is insufficient projector evidence.
    assert result['projector_tracks']==0
    assert not {'projector','both'} & set(modes)
    assert all(r['end']-r['start']>=30 for r in result['timeline'])
    board=f.analyze_samples([(t,surface(writing=min(6,t//10))[0]) for t in range(0,90,10)],90,mode='auto')
    assert board['projector_tracks']==0


def test_camera_move_recomputes_geometry_and_keeps_uncertain_interval_full():
    rows=[]
    for t in range(0,120,10):
        img,_=surface()
        if t>=50:img=cv2.warpAffine(img,np.float32([[1,0,30],[0,1,-20]]),(W,H),borderValue=(220,220,220))
        rows.append((t,img))
    result=f.analyze_samples(rows,120)
    assert result['camera_changes']
    assert any(r['mode']=='none' and r['start']<=50<=r['end'] for r in result['timeline'])
    assert result['timeline'][-1]['corners'] is not None


@pytest.mark.parametrize('correction',['rotate','perspective'])
def test_fit_contains_entire_quad_and_caps_local_pixel_scale(correction):
    _,truth=surface(angle=5,skew=True)
    # A 640px source cannot fill 1920px without either >1.5x or invented borders.
    # Framing requires real source pixels, so exercise the cap on a 4K source.
    geometry=f.fit(truth/[W,H],W*6,H*6,correction=correction,size=[1920,1080])
    h=np.array(geometry['matrix']);projected=f._project(truth*6,h)
    assert (projected>=-1e-3).all() and (projected<=[1920.001,1080.001]).all()
    assert geometry['scale']<=1.5001
    if correction=='perspective':
        assert abs(projected[0,1]-projected[1,1])<.01
        assert abs(projected[0,0]-projected[3,0])<.01


def test_hysteresis_is_conservative_and_transform_does_not_jitter():
    q=[[.1,.2],[.9,.2],[.9,.8],[.1,.8]]
    rows=[{'t':t,'epoch':0,'mode':'projector' if t==50 else 'board','targets':[q],'confidence':.9} for t in range(0,120,10)]
    timeline=f.stabilize(rows,120)
    assert all(r['end']-r['start']>=30 for r in timeline)
    analysis={'timeline':timeline,'confidence':.9,'uncertain_seconds':0,'samples':12,'camera_changes':[],'projector_tracks':1}
    plan=f.make_plan(analysis,W,H)
    assert np.allclose(f.matrix_at(plan,5),f.matrix_at(plan,15))
    assert f.source_time(7,[(10,15),(25,30)])==27


@pytest.mark.parametrize('mode',['auto','board','projector','off'])
@pytest.mark.parametrize('correction',['rotate','perspective'])
def test_config_accepts_only_explicit_framing_modes(mode,correction):
    config={'groups':{'G':{'subjects':[{'name':'S','playlist':''}],'slack_channel':'C','framing':mode,'framing_correction':correction}},'operator_slack_channel':'C','cut':{'min_silence_sec':180,'max_cut_ratio':.4}}
    assert validate_config(config)['groups']['G']['framing']==mode
    config['groups']['G']['framing_correction']='magic'
    with pytest.raises(ValueError,match='framing_correction'):validate_config(config)


def make_video(path,seconds=3,fps=8):
    process=subprocess.Popen(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{W}x{H}','-r',str(fps),'-i','pipe:0',
                              '-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={seconds}',
                              '-t',str(seconds),'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(path)],stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    for _ in range(seconds*fps):process.stdin.write(surface(angle=3,skew=True)[0].tobytes())
    process.stdin.close();stderr=process.stderr.read();assert process.wait(timeout=60)==0,stderr


@pytest.mark.parametrize('correction',['rotate','perspective'])
def test_real_render_uses_one_encode_and_preserves_cuts_audio_and_privacy_inputs(tmp_path,correction):
    from reel.stages.render import render,video_info
    src=tmp_path/'source.mp4';make_video(src)
    _,q=surface(angle=3,skew=True)
    analysis={'timeline':[{'start':0,'end':1.5,'mode':'board','epoch':0,'corners':(q/[W,H]).tolist(),'confidence':.9},
                          {'start':1.5,'end':3,'mode':'board','epoch':1,'corners':(q/[W,H]+[.01,0]).tolist(),'confidence':.9}],
              'confidence':.9,'uncertain_seconds':0,'samples':1,'camera_changes':[],'projector_tracks':0}
    plan=f.make_plan(analysis,W,H,correction=correction)
    result=render(src,[(0,.9),(1.5,3)],tmp_path/'final.mp4',framing=plan)
    assert result['framing_render']['video_encodes']==1
    info=video_info(result['final']);assert [info['width'],info['height']]==plan['output_size']
    assert info['codec']=='h264' and not info['hdr']
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',result['final']]))
    assert abs(float(probe['format']['duration'])-2.4)<.15
    assert any(x['codec_type']=='audio' for x in probe['streams'])


def test_transition_does_not_exceed_zoom_cap():
    rows=[]
    for start,angle in [(0,-14),(30,14)]:
        _,q=surface(angle=angle)
        rows.append({'start':start,'end':start+30,'mode':'board','epoch':start,'corners':(q/[W,H]).tolist(),'confidence':.9})
    analysis={'timeline':rows,'confidence':.9,'uncertain_seconds':0,'samples':6,'camera_changes':[],'projector_tracks':0}
    plan=f.make_plan(analysis,W,H)
    points=np.array([[x,y] for x in np.linspace(0,W,12) for y in np.linspace(0,H,12)])
    for t in np.linspace(30,31,31):
        matrix=f.matrix_at(plan,t);out=f._project(points,matrix)
        visible=points[(out[:,0]>=0)&(out[:,0]<=W)&(out[:,1]>=0)&(out[:,1]<=H)]
        assert f.magnification(matrix,visible)<=1.501


def test_invalid_geometry_and_empty_samples_preserve_full_frame():
    analysis=f.analyze_samples([(0,None),(10,None),(20,None)],30)
    assert analysis['timeline'][0]['mode']=='none'
    analysis['timeline'][0].update(mode='board',corners=[[float('nan'),0]]*4)
    plan=f.make_plan(analysis,W,H)
    assert plan['timeline'][0]['mode']=='none' and plan['uncertain_seconds']==30
    assert np.isfinite(f.matrix_at(plan,5)).all()


def test_render_blur_privacy_thumbnail_chain_uses_framed_pixels(tmp_path,monkeypatch):
    import reel.stages as stages
    import reel.blur as blur
    import reel.lecturer as lecturer
    import reel.thumbnail as thumbnail
    from reel.stages.render import render,video_info
    from reel.pipeline import Ctx
    src=tmp_path/'source.mp4';make_video(src)
    analysis=f.analyze_samples([(i,surface()[0]) for i in range(3)],3,mode='board')
    plan=f.make_plan(analysis,W,H)
    result=render(src,[(0,3)],tmp_path/'framed.mp4',framing=plan)
    seen=[]
    class FakeFaces:
        refs=[]
        def __init__(self,*args):pass
        def faces(self,image):
            seen.append(image.shape[:2]);return []
    monkeypatch.setattr(blur,'FaceID',FakeFaces)
    monkeypatch.setattr(lecturer,'discover',lambda source,*args,**kw: {'mode':'none'})
    ctx=Ctx(None,35,src,tmp_path,'G',{'groups':{'G':{'lecturers':[]}}})
    ctx._outputs['render']=result
    ctx._outputs['blur']=stages.blur(ctx)
    guarded=stages.privacy_stage(ctx)
    assert guarded['other_faces']==0 and seen
    info=video_info(ctx.out('blur')['final'])
    assert [info['width'],info['height']]==plan['output_size']
    captured=[]
    monkeypatch.setattr(thumbnail,'make_thumbnail',lambda source,*args,**kwargs: captured.append(source) or str(tmp_path/'thumbnail.jpg'))
    ctx._outputs['metadata']={'title':'Դաս 35'}
    stages.thumbnail(ctx)
    assert captured==[ctx.out('blur')['final']]


def test_static_board_widens_tiny_crop_for_scale_cap():
    from reel.board import plan
    result=plan([[.4,.4],[.6,.4],[.6,.6],[.4,.6]],1920,1080)
    assert result['output_size']==[1920,1080]
    assert result['cap_ok'] and result['crop'][2]>=1280


def test_projector_can_illuminate_board_without_making_outer_board_a_screen():
    frames=[]
    for t in range(0,100,10):
        image,_=surface(writing=0)
        x=270;split=390;right=510;top=110;bottom=230;on=t>=20
        if on:
            colour=(230,230,230) if t//20%2 else (70,70,70)
            inverse=(70,70,70) if t//20%2 else (230,230,230)
            cv2.rectangle(image,(x,top),(split,bottom),colour,-1)
            cv2.rectangle(image,(split,top),(right,bottom),inverse,-1)
        cv2.rectangle(image,(x,top),(right,bottom),(120,120,120),3)
        frames.append((t,image))
    result=f.analyze_samples(frames,100)
    # this projection is observed for <120s, so it stays unconfirmed.
    assert result['projector_tracks']==0
    assert all(r['mode'] not in ('projector','both') for r in result['timeline'])


def test_uncertain_camera_interval_does_not_swallow_long_confident_neighbours():
    q=[[.1,.2],[.9,.2],[.9,.8],[.1,.8]]
    rows=[{'t':t,'epoch':0 if t<110 else 1,'mode':'none' if t in (100,110) else 'board',
           'targets':[] if t in (100,110) else [q],'confidence':.9} for t in range(0,300,10)]
    timeline=f.stabilize(rows,300)
    assert [r['mode'] for r in timeline]==['board','none','board']
    assert timeline[1]['end']-timeline[1]['start']==60
