"""Spreadsheet import: upload → durable job ``ativos.import``, progress, error report, template."""

from __future__ import annotations

import asyncio
import secrets
import time
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.jobs import Job, enqueue_with
from hub_sdk.render import download_headers, html
from starlette.datastructures import UploadFile

from ativos import importer, views
from ativos.seed import int_env
from ativos.state import (
    DB_ERRORS,
    State,
    StateDep,
    audit_tx,
    base_of,
    db_notice,
    get_job,
    jobs_ready,
    user_of,
)
from ativos.ui import esc, notice
from ativos.views import not_found

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter()


def import_max_mb() -> int:
    """Maximum size of the ``.xlsx`` (``HUB_IMPORT_MAX_MB``, default 50); the gateway applies the
    ``limits.max_upload_mb`` from the manifest first."""
    return int_env("HUB_IMPORT_MAX_MB", 50)


def import_form(base: str, msg: str = "") -> str:
    return views.import_form(msg, import_max_mb(), importer.MAX_ROWS, base)


class ImportRefused(Exception):
    """Upload not accepted: the status and notice title/text to answer with."""

    def __init__(self, status: int, title: str, text: str) -> None:
        super().__init__(text)
        self.status, self.title, self.text = status, title, text


async def _xlsx_field(req: Request) -> tuple[UploadFile, str]:
    """The uploaded file and its printable name (``.xlsx`` only)."""
    try:
        form = await req.form(max_files=1, max_fields=10)
    except Exception as e:
        raise ImportRefused(400, "Envio interrompido", "O arquivo não pôde ser lido.") from e
    up = form.get("file")
    if not isinstance(up, UploadFile) or up.filename in (None, ""):
        raise ImportRefused(422, "Nenhum arquivo enviado", "Selecione uma planilha .xlsx.")
    name = "".join(ch for ch in up.filename if ch.isprintable())[:120] or "planilha.xlsx"
    if not name.lower().endswith(".xlsx"):
        raise ImportRefused(422, "Formato não suportado", "Envie um arquivo .xlsx (Excel 2007 ou superior).")
    return up, name


async def _write_upload(up: UploadFile, dest: Path, limit: int) -> int:
    """Copies the upload to ``dest`` checking the zip signature and the size limit; returns the bytes."""
    size = 0
    out = await asyncio.to_thread(partial(dest.open, "wb"))
    try:
        first = True
        while chunk := await up.read(1024 * 1024):
            if first and not chunk.startswith(b"PK\x03\x04"):
                raise ValueError("not-xlsx")
            first = False
            size += len(chunk)
            if size > limit:
                raise OverflowError
            await asyncio.to_thread(out.write, chunk)
    finally:
        await asyncio.to_thread(out.close)
    return size


async def _spool(up: UploadFile, dest: Path) -> int:
    try:
        size = await _write_upload(up, dest, import_max_mb() * 1024 * 1024)
    except ValueError as e:
        await asyncio.to_thread(partial(dest.unlink, missing_ok=True))
        raise ImportRefused(422, "Arquivo inválido", "O conteúdo não é uma planilha .xlsx.") from e
    except OverflowError as e:
        await asyncio.to_thread(partial(dest.unlink, missing_ok=True))
        raise ImportRefused(413, "Arquivo muito grande", f"O limite é {import_max_mb()} MB.") from e
    finally:
        await up.close()
    if size == 0:
        await asyncio.to_thread(partial(dest.unlink, missing_ok=True))
        raise ImportRefused(422, "Nenhum arquivo enviado", "Selecione uma planilha .xlsx.")
    return size


async def _enqueue(st: State, req: Request, payload: dict[str, str], size: int) -> tuple[Job, bool]:
    """Job + audit + idempotency marker in the same transaction."""
    ctx = idem_ctx(req)
    name = payload["file"]
    async with st.sdk.pool.acquire() as c, c.transaction():
        job, created = await enqueue_with(c, importer.SPEC, payload, user_of(req), ctx.key if ctx else None)
        if created:
            await audit_tx(c, req, "Importação de ativos iniciada", name, f"{size // 1024} KB")
        elif ctx is not None:
            await ctx.mark_committed(c)
    return job, created


async def _start(st: State, req: Request) -> Job:
    # The upload is only written to disk; the heavy-operation slot is taken by the worker (without
    # a slot, the job waits in the queue instead of getting a 429).
    up, name = await _xlsx_field(req)
    uid = f"{int(time.time() * 1000):x}{secrets.token_hex(2)}"
    dest = st.scratch / f"upload-{uid}.xlsx"
    report = st.scratch / f"import-{uid}-erros.csv"
    size = await _spool(up, dest)
    payload = {
        "file": name,
        "file_path": str(dest),
        "report_path": str(report),
        "login": identity_of(req).user,
    }
    try:
        job, created = await _enqueue(st, req, payload, size)
    except DB_ERRORS as e:
        dest.unlink(missing_ok=True)
        raise ImportRefused(503, "Não foi possível registrar a importação", str(e)) from e
    if not created:
        dest.unlink(missing_ok=True)  # resubmission of the same submission: the job already exists
    return job


@router.api_route("/ativos/itens/import", methods=["POST", "GET"])
async def start_import(req: Request, st: StateDep) -> Response:
    base = base_of(req)
    if req.method == "GET":
        return not_found(base)
    if (n := db_notice(st)) is not None:
        return html(import_form(base, n), 503)
    try:
        job = await _start(st, req)
    except ImportRefused as e:
        return html(import_form(base, notice("error", e.title, e.text)), e.status)
    st.sdk.wake()
    return html(views.job_panel(importer.job_view(job), base), 202)


@router.get("/ativos/itens/import/{job_id}")
async def import_status(job_id: str, req: Request, st: StateDep) -> Response:
    if not jobs_ready(st):
        n = db_notice(st) or ""
        return html(
            '<section class="content-panel hub-panel" id="import-panel" '
            f'data-hub-poll="{base_of(req)}/itens/import/{esc(job_id)}" data-hub-interval="3000">{n}</section>',
            503,
        )
    j = await get_job(st, job_id, importer.KIND)
    if j is None:
        msg = notice("warn", "Importação não encontrada", "Confira o link ou envie a planilha novamente.")
        return html(f'<section class="content-panel hub-panel" id="import-panel">{msg}</section>', 404)
    return html(views.job_panel(importer.job_view(j), base_of(req)))


@router.get("/ativos/itens/import/{job_id}/erros.csv")
async def import_errors(job_id: str, req: Request, st: StateDep) -> Response:
    j = await get_job(st, job_id, importer.KIND) if jobs_ready(st) else None
    if j is None:
        return not_found(base_of(req))
    p = Path(str((j.payload or {}).get("report_path", "")))
    if not await asyncio.to_thread(p.is_file):
        return not_found(base_of(req))
    return FileResponse(
        p, headers=download_headers("text/csv; charset=utf-8", f"erros-importacao-{j.id}.csv")
    )


@router.get("/ativos/itens/modelo.xlsx")
async def template_xlsx(st: StateDep) -> Response:
    p = st.scratch / "modelo-ativos.xlsx"
    if not p.exists():
        await asyncio.to_thread(importer.template_xlsx, p)
    return FileResponse(p, headers=download_headers(XLSX, "modelo-ativos.xlsx"))
