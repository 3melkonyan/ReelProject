"""Cut decisions survive pipeline adapters; smart pauses share the same safety budget."""
import json
from types import SimpleNamespace

from reel import stages
from reel.stages import processing,segments


def test_stage_preserves_structured_reason_and_boundary_evidence(tmp_path,monkeypatch):
    transcript=tmp_path/'transcript';transcript.mkdir();(transcript/'transcript.json').write_text('{"segments": []}')
    result={'keep':[[0.,900.]],'cuts':[],'original_sec':900.,'final_sec':900.,'cut_ratio':0.,'unclear_ratio':.45,
            'no_cut_code':'unclear','no_cut_details':{'percent':45.,'limit':30.},'labels':[{'label':'unclear'}],
            'edges':{'applied':False},'auto_note':'unclear windows 45.0% (> 30.0%) — nothing cut'}
    monkeypatch.setattr(segments,'plan',lambda *args:result)
    outputs={'probe':{'duration_sec':900.},'audio':{'wav':'unused.wav'},'join':{}}
    ctx=SimpleNamespace(config={'groups':{'G':{}}},group='G',workdir=tmp_path,media=lambda:'unused.mp4',out=outputs.get)
    value=stages.segments_stage(ctx)
    assert value['no_cut_code']=='unclear' and value['no_cut_details']=={'percent':45.,'limit':30.}
    assert value['labels']==result['labels'] and value['edges']==result['edges']
    assert value['cut_summary']['no_cuts_reason']=='Դասի 45%-ը հնարավոր չեղավ հստակ դասակարգել (սահմանը՝ 30%)․ ոչինչ չի կտրվել։'


def test_actual_smart_pause_clears_no_cut_reason_without_touching_speech(monkeypatch):
    monkeypatch.setattr(segments,'detect_silences',lambda *args:[(200.,205.)])
    monkeypatch.setattr(processing,'board_motion',lambda *args:0.)
    original={'keep':[[0.,900.]],'cuts':[],'original_sec':900.,'final_sec':900.,'cut_ratio':0.,'no_cut_code':'none_found'}
    speech={'segments':[{'start':0.,'end':100.,'text':'Դասի խոսքը պահպանվում է'}]}
    value=processing.pauses(original,'unused','unused',speech,{},processing.settings()['smart_pauses'],.4)
    assert len(value['pause_events'])==1 and 0<value['cut_ratio']<=.4
    assert value['no_cut_code']is None and value['no_cut_details']=={}
    assert value['time_map'][0]['source_start']==0. and value['time_map'][0]['source_end']>=100.
    assert original['no_cut_code']=='none_found' and original['final_sec']==900.
