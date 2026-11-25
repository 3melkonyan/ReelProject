"""The final G-28 plan still reaches render, chapter mapping and the cuts API."""
import json
import subprocess

import pytest

from reel import timeline
from reel.stages import segments, render, metadata
from test_web import web  # noqa: F401 - isolated temporary DB/work/test server


@pytest.fixture
def planned(tmp_path,monkeypatch):
    source=tmp_path/'transcript.json'
    source.write_text(json.dumps({'segments':[
        {'start':0,'end':.5,'text':'Բացատրություն'},
        {'start':.5,'end':2.5,'text':'Կողմնակի զրույց'},
        {'start':2.5,'end':4,'text':'Շարունակենք'},
    ]}))
    monkeypatch.setattr(segments,'detect_silences',lambda *a:[])
    monkeypatch.setattr(segments,'label_windows',lambda w:['lesson','offtopic','offtopic','offtopic','offtopic','lesson','lesson','lesson'])
    def review(system,task,rows):
        if not task.startswith('middle'):return {}
        return {'protected_ids':[0,2],'outside_ids':[1],'cuts':[
            {'after_id':0,'before_id':2,'kind':'offtopic','confidence':'high'}]}
    monkeypatch.setattr(segments,'_review',review)
    result=segments.plan('v','a',source,4,{'min_final_sec':0,'break_min_sec':1,'window_sec':.5,'max_cut_ratio':.6},tmp_path/'plan')
    assert result['keep']==[[0.,.5],[2.5,4]]
    return result


def test_real_render_accepts_sentence_plan_and_preserves_expected_duration(planned,tmp_path):
    src=tmp_path/'source.mp4';dest=tmp_path/'rendered.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=25:duration=4',
                    '-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=4',
                    '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(src)],
                   check=True,capture_output=True,timeout=30)
    render.render(src,planned['keep'],dest)
    info=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(dest)],
                                   check=True,capture_output=True,text=True,timeout=20).stdout)
    assert float(info['format']['duration'])==pytest.approx(planned['final_sec'],abs=.15)
    assert next(s for s in info['streams'] if s['codec_type']=='video')['codec_name']=='h264'
    assert next(s for s in info['streams'] if s['codec_type']=='audio')['codec_name']=='aac'


def test_existing_chapter_planner_uses_new_keep_without_removed_topics(planned):
    keep=[(a*1000,b*1000) for a,b in planned['keep']]
    topics=['Function Scope and this Binding','Dynamic this Resolution','Function Declaration',
            'Arrow Function Performance','Compiler Optimization','Interpreter Execution Model','Closure','Bytecode Generation']
    changes=[{'t':t,'topic':topic} for t,topic in zip([0,200,400,2500,2800,3100,3400,3800],topics)]
    changes.append({'t':1500,'topic':'RemovedTopic'})
    chapters=metadata._chapter_plan([{'start':0,'topic':topics[0],'topic_changes':changes}],keep)
    assert len(chapters)>=3 and all(c['t']!=1500 for c in chapters)
    assert all(c['final_t']==timeline.to_final(c['t'],keep) for c in chapters)
    assert chapters[-1]['final_t']<timeline.duration(keep)


@pytest.mark.parametrize('role',['admin','viewer'])
def test_real_cuts_api_exposes_exact_applied_intervals(planned,web,role):
    (web.work/'segments.json').write_text(json.dumps(planned))
    response=web.client(role).request('GET',f'/api/jobs/{web.job}')
    assert response.status==200
    artifact=response.json()['segments']
    assert artifact['keep']==planned['keep'] and artifact['cuts']==planned['cuts']
    assert artifact['final_sec']==2 and artifact['cut_ratio']==.5
    assert [(c['start'],c['end']) for c in artifact['cuts']]==timeline.subtract((0,4),[tuple(k) for k in artifact['keep']])
