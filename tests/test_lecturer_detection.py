"""Position is a candidate preference, never authority to bypass identity safety."""
import numpy as np
from reel import lecturer as lec
from test_lecturer import samples,PEOPLE,run


def plan():
    from reel.framing import make_plan
    return make_plan(dict(timeline=[dict(start=0,end=1000,mode='board',epoch=0,corners=[[.15,.1],[.85,.1],[.85,.55],[.15,.55]],confidence=.9)],confidence=.9,samples=1,camera_changes=[],projector_tracks=0),1920,1080)


def positioned(first=100,second=30):
    s=samples([(PEOPLE[0],range(first)),(PEOPLE[1],range(second))],frames=150)
    # Set coordinates in the actual rendered board zone, while others sit below it.
    s.size=(1920,1080)
    for i,v in enumerate(s.unit):
        s.box[i]=[800,200,40,40] if float(v@PEOPLE[0])>.8 else [800,1040,40,40]
    return s


def test_board_preference_keeps_dominant_identity(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch,positioned(),framing=plan())
    assert r['mode']=='auto'
    assert r['board_zone_shares']==[1.,0.]


def test_zone_never_bypasses_dominance_or_crowd(tmp_path,monkeypatch):
    assert run(tmp_path,monkeypatch,positioned(100,100),framing=plan())['mode']=='none'
    s=samples([(PEOPLE[0],range(100)),(PEOPLE[1],range(30)),(PEOPLE[2],range(30))],150)
    assert run(tmp_path,monkeypatch,s,framing=plan())['mode']=='none'


def test_no_plan_is_original_policy(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch,positioned())
    assert r['mode']=='auto' and r['board_zone_shares']==[None,None]


def test_full_resolution_sampler_keeps_previously_small_face(monkeypatch,tmp_path):
    seen=[]
    class Fake:
        def __init__(self,*a):pass
        def faces(self,image):
            seen.append(image.shape[1]);return [np.array([100,100,28,28,*([0]*10),.95])]
        def feature(self,image,face):return PEOPLE[0].reshape(1,128)
    def frames(video,every,width):
        assert width==1920
        yield np.zeros((1080,1920,3),np.uint8)
    monkeypatch.setattr(lec,'FaceID',Fake);monkeypatch.setattr(lec,'_keyframes',frames)
    s=lec._sample(tmp_path/'fake.mp4',2.)
    assert seen==[1920] and len(s.box)==1


def test_small_faces_blur_but_do_not_supply_identity_evidence():
    from reel import blur
    class Fake:
        refs=[True]
        def faces(self,image):return [np.array([80,80,18,18,*([0]*10),.95])]
        def feature(self,*args):raise AssertionError('Sub-24px identity must not be used')
    records=blur._identify(Fake(),np.zeros((1080,1920,3),np.uint8),blur._Tracker(),0.)
    blur._classify_analysis(records)
    assert records[0]['state']=='student'
    policy=blur._BlurPolicy([dict(t=0.,box=[200,200,60,60],lecturer=True,state='lecturer')])
    boxes,known,_=blur._detections(Fake(),np.zeros((1080,1920,3),np.uint8),lecturer_boxes=policy)
    assert boxes and known==0 and not policy.observed_unsure
