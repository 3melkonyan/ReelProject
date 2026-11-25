"""Immediate YouTube readiness polling, unchanged Slack text and local idempotency."""
from types import SimpleNamespace

import pytest

from reel import stages as st
from reel.db import DB
from reel.pipeline import Ctx, StopJob
from reel.publish import slack as slack_adapter


class Clock:
    def __init__(self):self.now=1000.0;self.sleeps=[]
    def monotonic(self):return self.now
    def sleep(self,seconds):
        assert 0<seconds<=15
        self.sleeps.append(seconds);self.now+=seconds


@pytest.fixture
def context(tmp_path,monkeypatch):
    d=DB(tmp_path/'fake.db')
    jid=d.create_job(str(tmp_path/'Դաս 13.mp4'),'g42-slack-fixture','G','Python')
    d.add_publication(jid,'youtube','abcdefghijk','https://youtu.be/abcdefghijk')
    outputs={'youtube_upload':{'external_id':'abcdefghijk','url':'https://youtu.be/abcdefghijk','lesson_no':13},
             'metadata':{'title':'Դասի թեման','description':'Նկարագրություն'}}
    c=Ctx(d,jid,tmp_path/'Դաս 13.mp4',tmp_path,'G',{'groups':{'G':{'subjects':[{'name':'Python','playlist':''}],'slack_channel':'Cfixture'}}},_outputs=outputs)
    monkeypatch.setattr(st,'DRY_RUN',False)
    try:yield c
    finally:d.conn.close()


def fake_publishers(monkeypatch,clock,statuses):
    events=[];remaining=iter(statuses) if not callable(statuses) else None
    def status(video):
        value=statuses(video) if callable(statuses) else next(remaining)
        events.append(('youtube',clock.now-1000,value));return value
    def post(channel,line,url):
        assert st.DRY_RUN or any(event[0]=='youtube' and event[2]=='processed' for event in events)
        events.append(('slack',clock.now-1000,channel,line,url));return '123.456'
    publishers={'youtube':SimpleNamespace(processing_status=status),'slack':SimpleNamespace(post_link=post)}
    monkeypatch.setattr(st,'_pub',lambda name:publishers[name])
    monkeypatch.setattr(st.time,'monotonic',clock.monotonic)
    monkeypatch.setattr(st.time,'sleep',clock.sleep)
    return events


def test_processed_first_check_posts_immediately_and_preserves_exact_message(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,['processed'])
    result=st.slack_post(context)
    assert st.YT_MIN_WAIT==0 and st.YT_POLL==15 and st.YT_MAX_WAIT==3600
    assert clock.sleeps==[] and events[0]==('youtube',0,'processed')
    assert events[1]==('slack',0,'Cfixture','Python դասընթացի 13-րդ դասի տեսանյութը տեղադրված է YouTube-ում','https://youtu.be/abcdefghijk')
    assert result['external_id']=='123.456' and result['reused'] is False


def test_processed_on_third_check_waits_exactly_30_seconds(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,['uploaded','uploaded','processed'])
    st.slack_post(context)
    assert clock.sleeps==[15,15]
    assert [event[1] for event in events if event[0]=='youtube']==[0,15,30]
    assert events[-1][0:2]==('slack',30)


@pytest.mark.parametrize('status',['failed','rejected','deleted','missing'])
def test_rejected_status_never_posts_slack(context,monkeypatch,status):
    clock=Clock();events=fake_publishers(monkeypatch,clock,[status])
    with pytest.raises(StopJob,match='did not accept'):st.slack_post(context)
    assert clock.sleeps==[] and all(event[0]!='slack' for event in events)
    assert context.db.publication(context.job_id,'slack') is None


def test_unknown_status_keeps_polling_until_processed(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,['unknown','processed'])
    st.slack_post(context)
    assert clock.sleeps==[15] and events[-1][0:2]==('slack',15)


def test_stuck_video_stops_at_60_minutes_without_posting(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,lambda video:'uploaded')
    with pytest.raises(StopJob,match='after 60 min'):st.slack_post(context)
    assert sum(clock.sleeps)==3600 and clock.now==4600
    assert len([event for event in events if event[0]=='youtube'])==240
    assert all(event[0]!='slack' for event in events)


def test_deadline_uses_monotonic_time_and_caps_the_final_sleep(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,lambda video:'uploaded')
    monkeypatch.setattr(st,'YT_MAX_WAIT',31)
    monkeypatch.setattr(st.time,'time',lambda:-1e9)
    with pytest.raises(StopJob,match='still processing'):st.slack_post(context)
    assert clock.sleeps==[15,15,1] and clock.now==1031
    assert all(event[0]!='slack' for event in events)


def test_processed_response_after_deadline_does_not_send(context,monkeypatch):
    clock=Clock()
    def too_late(video):clock.now+=3600;return 'processed'
    events=fake_publishers(monkeypatch,clock,too_late)
    with pytest.raises(StopJob,match='still processing'):st.slack_post(context)
    assert all(event[0]!='slack' for event in events) and clock.sleeps==[]


def test_dry_run_does_not_poll_or_wait(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,[])
    monkeypatch.setattr(st,'DRY_RUN',True)
    st.slack_post(context)
    assert clock.sleeps==[] and [event[0] for event in events]==['slack']


def test_published_slack_is_reused_without_polling_or_duplicate_message(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,['processed'])
    st.slack_post(context)
    first=list(events)
    assert st.slack_post(context)['reused'] is True
    assert events==first and clock.sleeps==[]
    assert not (context.workdir/'pub_slack.pending').exists()


def test_ambiguous_pending_slack_marker_stops_instead_of_resending(context,monkeypatch):
    clock=Clock();events=fake_publishers(monkeypatch,clock,['processed'])
    (context.workdir/'pub_slack.pending').write_text('pending')
    with pytest.raises(StopJob,match='may already be posted'):st.slack_post(context)
    assert all(event[0]!='slack' for event in events)
    assert (context.workdir/'pub_slack.pending').exists()


def test_slack_client_timeout_is_at_most_30_with_no_dotenv_read(monkeypatch):
    seen={}
    monkeypatch.setattr(slack_adapter,'dotenv_values',lambda *args,**kwargs:{'SLACK_BOT_TOKEN':'fake-not-a-real-token'})
    def fake_client(**kwargs):seen.update(kwargs);return object()
    monkeypatch.setattr(slack_adapter,'WebClient',fake_client)
    slack_adapter._client()
    assert 0<seen['timeout']<=30 and seen['retry_handlers']==[]
