"""Academy presentation endpoints, mounted independently at each design checkpoint."""

from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Request

from reel import academy
from reel.i18n import t as _t
from reel.web import accounts


def make_router(env, sections=("overview", "lessons", "stats")):
    router = APIRouter(prefix="/api/academy")

    def reader(request: Request):
        who = env["authenticated"](request)
        if who not in ("admin", "lecturer", "viewer", "uploader"):
            raise HTTPException(403, _t("academy_api.sign_in_as_a_lecturer_uploader_viewer"))
        return getattr(request.state, "principal", None) or accounts.PRINCIPAL.get()

    def call(function, principal, **filters):
        from reel.config import load_config

        try:
            config = load_config(str(env["ROOT"] / "config.yaml"))
        except ValueError:
            raise HTTPException(503, _t("academy_api.settings_cannot_be_read_right_now")) from None
        database = env["db"]()
        with closing(database.conn):
            try:
                return function(database, config, env["ROOT"], principal, **filters)
            except PermissionError:
                raise HTTPException(
                    403, _t("academy_api.you_do_not_have_permission_to_view")
                ) from None
            except LookupError:
                raise HTTPException(404, _t("academy_api.group_not_found")) from None
            except ValueError:
                raise HTTPException(400, _t("academy_api.choose_valid_filters")) from None

    if "overview" in sections:

        @router.get("/overview")
        def overview(principal=Depends(reader)):
            return call(academy.overview, principal)

    if "lessons" in sections:

        @router.get("/lessons")
        def lessons(
            group: str | None = None,
            subject: str | None = None,
            status: str = "all",
            principal=Depends(reader),
        ):
            return call(academy.lessons, principal, group=group, subject=subject, status=status)

    if "stats" in sections:

        @router.get("/stats")
        def statistics(
            group: str | None = None, subject: str | None = None, principal=Depends(reader)
        ):
            return call(academy.statistics, principal, group=group, subject=subject)

    return router
