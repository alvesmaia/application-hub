"""Exports: streaming CSV (heavy slot) and the XLSX durable job (status, download)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from hub_sdk.heavy import PermitStreamingResponse
from hub_sdk.idempotency import idem_ctx
from hub_sdk.jobs import enqueue_with
from hub_sdk.render import busy, download_headers, html

from ativos import exporter
from ativos.query import Filter
from ativos.routes.imports import XLSX
from ativos.state import DB_ERRORS, StateDep, db_notice, get_job, jobs_ready, user_of, values_from
from ativos.ui import notice
from ativos.views import not_found

router = APIRouter()


def export_error(e: str) -> Response:
    return html(notice("error", "Não foi possível exportar", e), 503)


@router.get("/ativos/itens/export.csv")
async def export_csv(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(n, 503)
    permit = st.sdk.heavy.try_acquire()
    if permit is None:
        return busy("A exportação", "Exportação em fila…")
    f = Filter.from_query(req.query_params)
    await st.sdk.emit(user_of(req), "Exportação de ativos (CSV)", "ativos.csv", f.describe())
    # The slot is returned at the end of the send, on client disconnect, or when the object is gone.
    return PermitStreamingResponse(
        exporter.csv_stream(st.sdk.pool, f),
        permit,
        headers=download_headers("text/csv; charset=utf-8", "ativos.csv"),
    )


@router.api_route("/ativos/itens/export.xlsx", methods=["POST", "GET"])
async def export_xlsx(req: Request, st: StateDep) -> Response:
    if req.method == "GET":
        return not_found()
    if (n := db_notice(st)) is not None:
        return html(n, 503)
    if st.sdk.jobs is None:
        return export_error("tarefas indisponíveis")
    form = values_from(await req.form())
    f = Filter.from_query(form)
    ctx = idem_ctx(req)
    await asyncio.to_thread(exporter.cleanup_old_exports, st.scratch)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            job, _ = await enqueue_with(c, exporter.SPEC, f.payload(), user_of(req), ctx.key if ctx else None)
            if ctx is not None:
                await ctx.mark_committed(c)
    except DB_ERRORS as e:
        return export_error(str(e))
    st.sdk.wake()
    return html(exporter.panel(job), 202)


@router.get("/ativos/itens/export/{job_id}")
async def export_status(job_id: str, st: StateDep) -> Response:
    if not jobs_ready(st):
        return export_error("banco indisponível")
    j = await get_job(st, job_id, exporter.KIND)
    if j is None:
        return html(notice("warn", "Exportação não encontrada", "Gere a exportação de novo."), 404)
    return html(exporter.panel(j))


@router.get("/ativos/itens/export/{job_id}/ativos.xlsx")
async def export_download(job_id: str, st: StateDep) -> Response:
    j = await get_job(st, job_id, exporter.KIND) if jobs_ready(st) else None
    if j is None or j.status != "done":
        return not_found()
    p = exporter.out_path(st.scratch, j.id)
    if not await asyncio.to_thread(p.is_file):
        msg = notice(
            "warn",
            "Arquivo expirado",
            "O arquivo desta exportação já foi removido. Gere a exportação de novo.",
        )
        return html(msg, 410)
    return FileResponse(p, headers=download_headers(XLSX, "ativos.xlsx"))
