import json
from contextlib import closing
from reel.db import DB
from test_two_factor import begin



def test_real_second_factor_events_are_secret_free(secured):
    web,codes=secured;c=begin(web);code=codes[-1][1]
    wrong='000000' if code!='000000' else '000001'
    assert c.call('POST','/api/admin-auth/verify',body={'code':wrong})[0]==401
    assert c.call('POST','/api/admin-auth/verify',body={'code':code})[0]==200
    with closing(DB(web.root/'reel.db').conn) as conn:
        rows=[dict(r) for r in conn.execute("SELECT action,details,target FROM audit_events WHERE action LIKE 'auth.%'")]
    assert {'auth.challenge','auth.failed','auth.verified'}<={r['action'] for r in rows}
    assert all(set(json.loads(r['details']))<={'status','reason'} for r in rows)
    assert '@' not in str(rows) and 'code_hash' not in str(rows)
