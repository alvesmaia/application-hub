"""Packages (admins): list and upload (multipart zip, checked; versions are immutable)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.locale import t
from starlette.datastructures import UploadFile

from automations import packages, processes
from automations.access import is_admin
from automations.package_format import MAX_ZIP_BYTES, PackageError
from automations.state import DB_ERRORS, State, StateDep, mark_committed, see_other, user_of

router = APIRouter()


async def listing(st: State, req: Request, status: int = 200, errors: list[str] | None = None) -> Response:
    items, used = [], {}
    if st.db_ok():
        try:
            items = await packages.list_packages(st.pool)
            for p in await processes.list_processes(st.pool, only_enabled=False):
                used.setdefault((p.package_name, p.package_version), []).append(p.name)
        except DB_ERRORS:
            items = []
    return st.page(req, "pacotes.html", "packages", status, items=items, used=used, errors=errors or [])


@router.get("/automations/packages")
async def packages_page(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    return await listing(st, req)


async def read_upload(req: Request, field: str = "arquivo") -> bytes | None:
    """The uploaded file, read up to one byte past the limit (the check reports the size)."""
    f = (await req.form()).get(field)
    if not isinstance(f, UploadFile):
        return None
    return await f.read(MAX_ZIP_BYTES + 1)


@router.post("/automations/packages")
async def upload(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    data = await read_upload(req)
    if data is None:
        return await listing(st, req, 422, [t("Escolha um arquivo .zip.")])
    if not st.db_ok():
        return await listing(st, req, 503)
    try:
        async with st.pool.acquire() as c, c.transaction():
            await packages.register(c, data, user_of(req))
            await mark_committed(c, req)
    except PackageError as e:
        return await listing(st, req, 422, e.errors)
    except packages.DuplicateVersion as e:
        return await listing(st, req, 409, [str(e)])
    return see_other("/packages")
