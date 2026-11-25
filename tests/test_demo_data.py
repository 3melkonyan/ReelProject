from contextlib import closing
import json

import yaml
from reel.db import DB
from reel.demo import job_cards,read_demo_lessons

from tools import demo_seed as seed


def test_v2_safe_subjects_emails_and_reversible_v1_roundtrip(tmp_path):
    root=tmp_path
    config={'groups':{'Group-A':{'subjects':[{'name':'Group-A','playlist':'PL-preserve-this'}],'slack_channel':'C123','lecturers':[]}},'operator_slack_channel':'C123','cut':{'min_silence_sec':120,'max_cut_ratio':.3}}
    (root/'config.yaml').write_text(yaml.safe_dump(config))
    with closing(DB(root/'reel.db').conn) as conn:
        conn.execute("INSERT INTO jobs(source_path,sha256,grp,subject,created_at,updated_at) VALUES('fake','hash','Group-A','Group-A','2026-11-20','2026-11-20')")
    # Queued recordings are deliberately not migrated.
    with closing(DB(root/'reel.db').conn) as conn:conn.execute("UPDATE jobs SET status='done'")
    seed.apply(root)
    updated=yaml.safe_load((root/'config.yaml').read_text())
    assert updated['groups']['AI 2.6']['subjects'][0]=={'name':'Python','playlist':'PL-preserve-this'}
    with closing(DB(root/'reel.db').conn) as conn:
        from types import SimpleNamespace
        d=SimpleNamespace(conn=conn)
        rows=read_demo_lessons(d);assert len(rows)==57 and 'Group-A' not in str(rows)
        assert conn.execute('SELECT subject FROM jobs').fetchone()[0]=='Python'
        assert all('.' in row[0].split('@')[0] and row[0].endswith('@example.com') for row in conn.execute("SELECT email FROM users WHERE demo_tag=?",(seed.TAG,)))
        assert len(job_cards(d))==57 and all(not r['has_video'] for r in job_cards(d))
        # Simulate a persisted v1 run: remove/apply is the supported non-destructive upgrade path.
        run=conn.execute("SELECT payload_json FROM demo_registry WHERE kind='run'").fetchone()[0];payload=json.loads(run);payload.pop('version')
        conn.execute("UPDATE demo_registry SET payload_json=? WHERE kind='run'",(json.dumps(payload),))
    assert seed.apply(root)['upgrade_required']
    assert seed.remove(root)['status']=='removed'
    assert yaml.safe_load((root/'config.yaml').read_text())==config
    seed.apply(root);assert not seed.apply(root)['upgrade_required']
    assert seed.remove(root)['status']=='removed'
