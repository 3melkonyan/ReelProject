import json
import sqlite3
import numpy as np
import pytest
from reel.stages import processing as p
from reel.stages.board_enhance import BoardEnhancer
from reel.stages import ocr


def plan():
    return dict(version=2,size=[320,180],output_size=[320,180],transition_sec=0,
                timeline=[dict(start=0,end=100,mode='board',confidence=.95,
                               corners=[[.1,.1],[.9,.1],[.9,.9],[.1,.9]],matrix=np.eye(3).tolist())])


def test_opt_in_settings_copied_and_validated():
    a=p.settings();assert not any(x['enabled'] for x in a.values())
    a['ocr']['enabled']=True;assert not p.settings()['ocr']['enabled']
    for value in ({'unknown':{}},{'ocr':{'enabled':1}},{'smart_pauses':{'writing_speed':3}},
                  {'thumbnail':{'variant':1.5}},{'board_enhance':{'amount':float('nan')}}):
        with pytest.raises(ValueError):p.settings(value)


def test_final_time_map_speed_and_silence_crossfade():
    events=[dict(start=3,end=7,operation='pause',retain_sec=1),dict(start=10,end=14,operation='speed',speed=2)]
    rows=p.make_map([(0,20)],events)
    assert rows[-1]['final_end']==pytest.approx(14.92)
    assert p.to_final(5,rows) is None
    assert p.to_final(12,rows)==pytest.approx(7.92)
    assert p.to_source(p.to_final(16,rows),rows)==pytest.approx(16)
    assert 'acrossfade' in p.retime_graph(rows)
    assert 'xfade' in p.retime_graph(rows,video=True)


def test_smart_pauses_preserve_writing_unknown_speech_and_ratio(monkeypatch):
    from reel.stages import segments
    base=dict(keep=[[0,40]],cuts=[],original_sec=40,final_sec=40,cut_ratio=0,unclear_ratio=0)
    monkeypatch.setattr(segments,'detect_silences',lambda *a:[(3,7),(10,14),(20,24),(30,34)])
    monkeypatch.setattr(p,'board_motion',lambda _,a,*rest: None if a>30 else 5 if a>20 else 0)
    transcript={'segments':[{'start':10,'end':14,'text':'խոսք'}]}
    r=p.pauses(base,'video','audio',transcript,plan(),p.settings()['smart_pauses'],.3)
    assert [e['operation'] for e in r['pause_events']]==['pause','speed']
    assert r['cut_ratio']<=.3
    r=p.pauses(base,'v','a',transcript,plan(),p.settings()['smart_pauses'],.001)
    assert 'time_map' not in r
    assert p.pauses({**base,'auto_note':'fallback'},'v','a',transcript,plan(),p.settings()['smart_pauses'],.3)['keep']==base['keep']


def test_board_face_and_outside_pixels_untouched():
    yy,xx=np.indices((180,320));image=(170+(xx%7)*5).astype(np.uint8)
    # Detector's 320px image coordinates; protect a static face plus margin.
    detector=lambda _:np.array([[130,60,30,35]])
    enhance=BoardEnhancer(plan(),{'amount':.3},detector)
    enhance.apply(image,0);out=enhance.apply(image,1)
    assert np.array_equal(out[:18],image[:18])
    assert np.array_equal(out[40:110,115:175],image[40:110,115:175])
    assert np.any(out[25:45,40:100]!=image[25:45,40:100])
    broken=BoardEnhancer(plan(),{'amount':.3},lambda _:1/0)
    assert np.array_equal(broken.apply(image,1),image)


def test_projector_never_board_enhanced():
    pl=plan();pl['timeline'][0]['mode']='projector'
    image=np.random.default_rng(7).integers(16,235,(180,320),dtype=np.uint8)
    e=BoardEnhancer(pl,{'amount':.4},lambda _:[])
    assert np.array_equal(e.apply(image,0),image)


def test_ocr_storage_idempotent_atomic_and_final_time():
    conn=sqlite3.connect(':memory:',isolation_level=None)
    conn.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)');conn.execute('INSERT INTO jobs VALUES(1)')
    rows=[dict(t_start=2,t_end=5,source='projector',text='Հայերեն Python')]
    ocr.save(conn,1,rows);ocr.save(conn,1,rows)
    assert conn.execute('SELECT * FROM ocr_segments').fetchall()==[(1,2.,5.,'projector','Հայերեն Python')]
    with pytest.raises(ValueError):ocr.save(conn,1,[{**rows[0],'t_end':1}])
    assert conn.execute('SELECT COUNT(*) FROM ocr_segments').fetchone()[0]==1


def test_remap_transcript_and_words(tmp_path):
    path=tmp_path/'original.json';out=tmp_path/'final.json'
    path.write_text(json.dumps({'segments':[dict(start=10,end=12,text='hello',words=[dict(start=10,end=11,word='hello')])]}))
    rows=p.make_map([(5,15)],[dict(start=9,end=13,operation='speed',speed=2)])
    p.remap_transcript(path,rows,out)
    s=json.loads(out.read_text())['segments'][0]
    assert (s['start'],s['end'])==(4.5,5.5)
    assert s['words'][0]['end']==5


def test_ocr_chapters_keep_description_footer():
    m=dict(chapters=[dict(t=0,final_t=0,title='Intro')],youtube_description='Description\n\n00:00 Intro\n\nFooter')
    from reel.stages.segments import fmt
    m['youtube_description']=f"Description\n\n{fmt(0)} Intro\n\nFooter"
    r=ocr.add_chapters(m,[dict(final_t=10,title='Նոր սլայդ')])
    assert r['chapters'][-1]['final_t']==10
    assert 'Նոր սլայդ' in r['youtube_description'] and r['youtube_description'].endswith('Footer')


@pytest.mark.parametrize('variant',[0,1,2])
def test_thumbnail_explicit_lesson_fields(variant):
    from PIL import Image
    from reel.thumbnail import render_lesson_card
    image=render_lesson_card(Image.new('RGB',(640,360),'gray'),
                            dict(course='AI',group='AI 2.6',lesson=4,subject='Python'),variant)
    assert image.size==(1280,720)
    assert image.resize((320,180)).getbbox()


def test_studio_filter_disabled_and_gentle():
    assert p.studio_filter(p.settings()['studio_audio'])==''
    text=p.studio_filter(p.settings({'studio_audio':{'enabled':True}})['studio_audio'])
    assert 'afftdn=nr=6.0' in text and 'ratio=1.2' in text


def test_single_encode_retimed_video_audio_agree(tmp_path):
    import subprocess, shutil
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg not installed')
    from reel.framing_render import render
    from reel.stages.render import video_info
    src=tmp_path/'src.mp4';out=tmp_path/'out.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=10:d=8',
                    '-f','lavfi','-i','sine=frequency=440:duration=8','-c:v','libx264','-preset','ultrafast',
                    '-c:a','aac',str(src)],check=True)
    rows=p.make_map([(0,8)],[dict(start=1,end=4,operation='pause',retain_sec=1),dict(start=5,end=7,operation='speed',speed=2)])
    result=render(src,[(0,8)],out,plan(),video_info(src),'anull',{'input_i':-20},codec='libx264',time_map=rows)
    assert result['framing_render']['video_encodes']==1
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out)]))
    durations=[float(s['duration']) for s in probe['streams']]
    assert max(durations)-min(durations)<.15
    assert max(durations)==pytest.approx(rows[-1]['final_end'],abs=.15)


def test_ocr_projector_changes_and_final_time(tmp_path):
    import cv2
    path=tmp_path/'slides.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),1,(320,180))
    for t in range(30):
        frame=np.full((180,320,3),50 if t<10 else 200,np.uint8)
        writer.write(frame)
    writer.release()
    pl=plan();pl['timeline'][0]['mode']='projector'
    calls=[]
    def reader(image,minimum):
        calls.append(image.mean());return 'Առաջին slide' if image.mean()<100 else 'Երկրորդ slide'
    result=ocr.extract(path,pl,p.settings()['ocr'],recognizer=reader)
    assert len(calls)==2
    assert [c['final_t'] for c in result['chapters']]==[0.,10.]
    assert result['segments'][-1]['t_end']==30


def test_white_balance_face_mask_excluded_and_green_preserved():
    e=BoardEnhancer(plan(),{'amount':.3},lambda _:np.array([[130,60,30,35]]))
    image=np.full((180,320),200,np.uint8);e.apply(image,0);e.apply(image,1)
    u=np.full((90,160),140,np.uint8);v=u.copy();e.chroma(u,v)
    assert u[15,20]<140
    assert u[40,70]==140 and u[0,0]==140
    green=np.full_like(u,170);e.chroma(green,v)
    assert np.all(green==170)


def test_many_pause_joins_do_not_accumulate_av_drift(tmp_path):
    import subprocess,shutil
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg not installed')
    from reel.framing_render import render
    from reel.stages.render import video_info
    src=tmp_path/'many.mp4';out=tmp_path/'many-out.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=10:d=40',
                    '-f','lavfi','-i','sine=duration=40','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(src)],check=True)
    events=[dict(start=i+.23,end=i+3.39,operation='pause',retain_sec=1.07) for i in range(1,35,5)]
    rows=p.make_map([(0,40)],events)
    render(src,[(0,40)],out,plan(),video_info(src),'anull',{'input_i':-20},codec='libx264',time_map=rows)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out)]))
    ds=[float(s['duration']) for s in info['streams']]
    assert abs(ds[0]-ds[1])<.15
    assert ds[0]==pytest.approx(rows[-1]['final_end'],abs=.15)


def test_group_processing_config_is_validated():
    from reel.config import validate_config
    config=dict(groups={'AI 2.6':dict(slack_channel='C123',playlist_id='PL123',
                  processing={'ocr':{'enabled':True}})},operator_slack_channel='C123',cut={})
    # Reuse the repository's valid cut fixture; validation rejects unknown options first.
    config['groups']['AI 2.6']['processing']['ocr']['enabled']='yes'
    with pytest.raises(ValueError,match='enabled must be boolean'):validate_config(config)


def test_enabled_audio_reaches_render_and_keeps_video_source(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from reel import stages as st
    from reel.stages import render as rd
    seen={}
    ctx=SimpleNamespace(config={'groups':{'G':{'framing':'off','processing':{'studio_audio':{'enabled':True}}}}},
                        group='G',workdir=tmp_path,media=lambda:tmp_path/'source.mp4',
                        out=lambda stage:{'keep':[[0,20]]})
    monkeypatch.setattr(st,'_framing',lambda *_:(None,{}))
    def fake_render(src,keep,out,**kwargs):
        seen.update(kwargs);assert src==ctx.media();return {'final':str(out)}
    monkeypatch.setattr(rd,'render',fake_render);monkeypatch.setattr(rd,'measured_integrated',lambda _:-16)
    result=st.render_stage(ctx)
    assert seen['processing']['studio_audio']['enabled']
    assert result['final']==str(tmp_path/'final.mp4')


def test_measurement_retimes_even_without_audio_cleanup(monkeypatch):
    from types import SimpleNamespace
    from reel.stages import render as rd
    seen=[]
    monkeypatch.setattr(rd,'observed_run',lambda args,**kwargs:(seen.append(args) or SimpleNamespace(stderr='{"input_i":"-20"}')))
    rows=p.make_map([(0,10)],[dict(start=3,end=7,operation='speed',speed=2)])
    rd.measure_loudness('unused',[(0,10)],time_map=rows)
    graph=seen[0][seen[0].index('-filter_complex')+1]
    assert 'atempo=2.000000' in graph and 'loudnorm' in graph
