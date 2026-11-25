"""Read-only metadata-only demo lessons for the separate library implementation."""

from reel.i18n import t as _t
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Request

from reel.web import accounts


def make_router(env):
    router = APIRouter()

    def reader(request: Request):
        if env['authenticated'](request) not in ('admin', 'viewer', 'student', 'lecturer'):
            raise HTTPException(403, _t('demo_api.demo_lessons_are_not_available_to_this'))

    @router.get('/api/demo/lessons', dependencies=[Depends(reader)])
    def lessons():
        from reel.demo import read_demo_lessons
        database = env['db']()
        with closing(database.conn):
            return read_demo_lessons(database, groups=accounts.groups())

    return router
