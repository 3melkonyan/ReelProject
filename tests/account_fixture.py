"""Seed an EXISTING personal admin for account API regressions, only in pytest temp roots."""
from pathlib import Path
import tempfile
from reel.db import DB
from reel.web.accounts import create_user


def existing_admin(module):
    root=Path(module.ROOT).resolve()
    assert root.is_relative_to(Path(tempfile.gettempdir()).resolve()),'test account escaped temp root'
    async def completed_second_factor(request,row,d,env):
        from reel.web.two_factor import finish
        return finish(request,row,d)
    module._admin_login=completed_second_factor
    d=DB(root/'reel.db')
    try:
        if not d.conn.execute('SELECT count(*) FROM users').fetchone()[0]:
            create_user(d,'fixture-admin',module.SECRETS['admin_password'],
                        {'name':'Փորձնական ադմին','email':'admin@example.test','role':'admin','groups':[]})
    finally:d.conn.close()
