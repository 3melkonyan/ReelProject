import json,time
from reel.db import DB
from reel.lesson_report import poll
from reel.web.accounts import create_user


def test_group_scoped_report_once_and_no_previous_lesson_mass_email(tmp_path):
    d=DB(tmp_path/'test.db')
    for login,role,group in [('one','lecturer','A'),('two','lecturer','B'),('admin','admin','A')]:
        create_user(d,login+'-user','strong-password-123',dict(name=login,email=login+'@example.test',role=role,groups=[group] if role!='admin' else []))
    jid=d.create_job('video','hash','A');d.set_job(jid,status='done')
    sent=[]
    def send(*args):
        return sent.append(args) or True
    assert poll(d.conn,tmp_path,send=send,now=time.time()+5)==0
    # A completion after the persisted watermark is picked up after monitor downtime.
    d.conn.execute("UPDATE jobs SET updated_at=datetime('now','+10 seconds') WHERE id=?",(jid,))
    for name,value in [('segments',dict(original_sec=600,final_sec=480)),('metadata',dict(title='Python',description='Դասի ամփոփում։')),
                       ('render',dict(loudness_in=-38,board='full frame kept')),('blur',dict(blurred_boxes=10,student_tracks=1,unsure_tracks=2))]:
        d.conn.execute('INSERT INTO stages(job_id,name,status,output) VALUES(?,?,?,?)',(jid,name,'done',json.dumps(value)))
    folder=tmp_path/'work'/str(jid)/'transcript';folder.mkdir(parents=True)
    (folder/'transcript.json').write_text(json.dumps({'segments':[{'text':'Ինչպե՞ս է աշխատում Python-ը։'}]}))
    assert poll(d.conn,tmp_path,send=send)==1
    assert poll(d.conn,tmp_path,send=send)==0
    create_user(d,'new-teacher','strong-password-123',dict(name='Նոր դասախոս',email='new@example.test',role='lecturer',groups=['A']))
    assert poll(d.conn,tmp_path,send=send)==0 # new assignments do not mass-mail previous lesson reports
    assert sent[0][0]=='one@example.test'
    report=sent[0][2];assert report['minutes_cut']==2 and report['questions']
    assert any('թույլ' in x for x in report['warnings']) and any('track' in x for x in report['warnings'])
    d.conn.close()
