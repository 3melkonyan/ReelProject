"""Group-scoped watch statistics; shared passwords never grant named student analytics."""

from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Request

from reel import watch_statistics
from reel.i18n import t as _t
from reel.insights import library_records
from reel.web import accounts


def make_router(env):
    router = APIRouter(prefix="/api/library")

    def personal(request: Request):
        env["authenticated"](request)
        principal = getattr(request.state, "principal", None) or accounts.PRINCIPAL.get()
        if not principal or principal.get("legacy") or type(principal.get("id")) is not int:
            raise HTTPException(
                403, _t("watch_statistics_api.viewing_statistics_are_available_only_to_personal")
            )
        return principal

    @router.get("/watch-stats")
    def statistics(group: str | None = None, principal=Depends(personal)):
        from reel.config import load_config

        try:
            known = load_config(str(env["ROOT"] / "config.yaml"))["groups"]
        except ValueError:
            known = {}
        database = env["db"]()
        with closing(database.conn):
            try:
                # This shared reader enforces successful privacy and genuine YouTube publication.
                records = library_records(
                    database, env["ROOT"] / "library.jsonl", groups=accounts.groups()
                )
                return watch_statistics.statistics(
                    database, records, principal, group=group, known_groups=known
                )
            except PermissionError:
                raise HTTPException(
                    403, _t("watch_statistics_api.you_do_not_have_permission_to_view")
                ) from None
            except LookupError:
                raise HTTPException(404, _t("academy_api.group_not_found")) from None
            except ValueError:
                raise HTTPException(400, _t("watch_statistics_api.invalid_group_filter")) from None

    return router
