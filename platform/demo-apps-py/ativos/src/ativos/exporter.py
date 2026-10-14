"""Registration exports respecting search/filters/sorting.

- **CSV streaming**: server-side cursor straight into the response (constant memory); holds a
  heavy-operation slot until the end of the body (no slot: 429 + ``Retry-After``).
- **XLSX** as a durable job (``ativos.export_xlsx``): openpyxl *write-only* in ``/scratch``;
  the panel tracks progress and offers the download when it finishes (6 h). A new attempt
  starts over from scratch. Both cap the rows (``HUB_EXPORT_MAX_ROWS``, default 1 million).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from hub_sdk import outbox
from hub_sdk.jobs import Job, JobCtx, JobSpec, label
from hub_sdk.render import BOM, csv_line

from ativos.model import COLS, display
from ativos.query import Filter
from ativos.ui import esc, notice, thousands

KIND = "ativos.export_xlsx"
SPEC = JobSpec(KIND, max_attempts=3, max_duration=1800)
FETCH = 2000
UPLOAD_ORPHAN_S = 3600.0
UPLOAD_NO_DB_S = 24 * 3600.0

SELECT_COLS = ", ".join(c.key for c in COLS)


def max_rows() -> int:
    try:
        return int(os.environ.get("HUB_EXPORT_MAX_ROWS", "1000000"))
    except ValueError:
        return 1_000_000


def select_sql(f: Filter, cap: int) -> tuple[str, list[str]]:
    wh, params = f.where()
    return f"SELECT {SELECT_COLS} FROM assets {wh} ORDER BY {f.order()} LIMIT {cap}", params


def _row_values(r: Any) -> list[str]:
    return [display(c, r[c.key]) for c in COLS]


async def csv_stream(pool: Any, f: Filter) -> AsyncIterator[bytes]:
    """Generates the CSV (``;``, UTF-8 BOM, headers equal to the import's). The heavy slot belongs to the
    response (``PermitStreamingResponse``), not to the generator."""
    yield (BOM + csv_line([c.header for c in COLS])).encode()
    sql, params = select_sql(f, max_rows())
    async with pool.acquire() as c, c.transaction():
        buf: list[str] = []
        async for r in c.cursor(sql, *params, prefetch=FETCH):
            buf.append(csv_line(_row_values(r)))
            if len(buf) >= FETCH:
                yield "".join(buf).encode()
                buf.clear()
        if buf:
            yield "".join(buf).encode()


def out_path(scratch: Path, job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch == "-")
    return scratch / f"export-{safe}.xlsx"


def filter_of(job: Job) -> Filter:
    raw = (job.payload or {}).get("filter") or {}
    return Filter.from_query({k: str(v) for k, v in raw.items()})


class ExportHandler:
    spec = SPEC

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch

    async def run(self, ctx: JobCtx) -> Any:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font

        f = filter_of(ctx.job)
        sql, params = select_sql(f, max_rows())
        out = out_path(self.scratch, ctx.job.id)
        tmp = out.with_suffix(".part")
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Ativos")
        bold = Font(bold=True)
        head = []
        for col in COLS:
            cell = WriteOnlyCell(ws, value=col.header)
            cell.font = bold
            head.append(cell)
        ws.append(head)
        fmt = {"date": "dd/mm/yyyy", "money": "#,##0.00"}

        def line(r: Any) -> list[Any]:
            cells: list[Any] = []
            for col in COLS:
                v = r[col.key]
                if v is None:
                    cells.append(None)
                    continue
                if col.kind == "money":
                    v = int(v) / 100
                elif col.kind == "text" and v[:1] in ("=", "+", "-", "@"):
                    v = "'" + v  # no formulas coming from data
                cell = WriteOnlyCell(ws, value=v)
                if col.kind in fmt:
                    cell.number_format = fmt[col.kind]
                cells.append(cell)
            return cells

        n = 0
        async with ctx.pool.acquire() as c, c.transaction():
            batch: list[list[Any]] = []
            async for r in c.cursor(sql, *params, prefetch=FETCH):
                batch.append(line(r))
                if len(batch) >= FETCH:
                    await asyncio.to_thread(_append, ws, batch)
                    n += len(batch)
                    batch = []
                    ctx.raise_if_stopping()
                    await ctx.progress(n, 0, f"{n} linhas")
            if batch:
                await asyncio.to_thread(_append, ws, batch)
                n += len(batch)
        await asyncio.to_thread(wb.save, tmp)
        tmp.replace(out)
        return {"rows": n, "bytes": out.stat().st_size}

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None:
        desc = str((job.payload or {}).get("describe", ""))
        if status == "done":
            r = job.result if isinstance(job.result, dict) else {}
            rows = r.get("rows", job.progress_done)
            return outbox.audit(
                job.user, "Exportação de ativos (Excel)", "ativos.xlsx", f"{rows} linhas · {desc}"
            )
        return outbox.audit(
            job.user, "Exportação de ativos (Excel) falhou", "ativos.xlsx", f"{label(status)} · {detail}"
        )

    def after_final(self, job: Job, status: str) -> None:
        """Terminal unsuccessful attempt: deletes the partial file (the finished ``.xlsx`` stays
        available for download until the 6 h cleanup)."""
        if status != "done":
            out = out_path(self.scratch, job.id)
            for p in (out.with_suffix(".part"), out):
                with contextlib.suppress(OSError):
                    p.unlink()


def _append(ws: Any, rows: list[list[Any]]) -> None:
    for r in rows:
        ws.append(r)


def panel(j: Job) -> str:
    """Export panel (polling while running; download link when finished)."""
    jid = esc(j.id)
    if j.status in ("queued", "running"):
        queued = j.status == "queued"
        retry = f" · tentativa {j.attempts} de {j.max_attempts}" if j.attempts > 1 else ""
        return (
            f'<section class="content-panel hub-panel" id="export-panel" data-hub-poll="/apps/ativos/itens/export/{jid}" '
            f'data-hub-interval="1000"><div class="hub-panel-head"><div><span class="eyebrow">'
            f"{'EXPORTAÇÃO NA FILA' if queued else 'EXPORTAÇÃO EM ANDAMENTO'}</span><h2>ativos.xlsx</h2>"
            f'<p class="hub-muted">{thousands(j.progress_done)} linhas gravadas · {j.elapsed_s:.0f} s{retry}</p></div></div>'
            f'<div class="hub-progress"><i style="width:{0 if queued else 50}%"></i></div>'
            '<p class="hub-muted">A planilha é gerada em segundo plano (sobrevive a reinícios do app); '
            "o download aparece aqui ao concluir.</p></section>"
        )
    if j.status == "done":
        rows = int((j.result or {}).get("rows", 0)) if isinstance(j.result, dict) else 0
        return (
            '<section class="content-panel hub-panel" id="export-panel"><div class="hub-panel-head"><div>'
            f'<span class="eyebrow">EXPORTAÇÃO CONCLUÍDA</span><h2>ativos.xlsx</h2><p class="hub-muted">{thousands(rows)} linhas · '
            f'{j.elapsed_s:.1f} s</p></div><a class="primary-button" download href="/_frag/ativos/itens/export/{jid}/ativos.xlsx">'
            "Baixar planilha</a></div></section>"
        )
    return (
        '<section class="content-panel hub-panel" id="export-panel">'
        + notice("error", "Não foi possível exportar", f"{label(j.status)} · {j.error or ''}")
        + "</section>"
    )


def cleanup_old_exports(
    scratch: Path, max_age_s: float = 6 * 3600, active_uploads: set[str] | None = None
) -> int:
    """Deletes from ``/scratch``: exports (``export-*.xlsx``/``.part``) older than ``max_age_s``;
    uploads (``upload-*.xlsx``) outside ``active_uploads`` and older than 1 h — or, without the list
    (DB down), older than 24 h."""
    n = 0
    now = time.time()
    old: list[tuple[Path, float]] = [
        (p, max_age_s) for p in [*scratch.glob("export-*.xlsx"), *scratch.glob("export-*.part")]
    ]
    for p in scratch.glob("upload-*.xlsx"):
        if active_uploads is None:
            old.append((p, UPLOAD_NO_DB_S))
        elif str(p) not in active_uploads:
            old.append((p, UPLOAD_ORPHAN_S))
    for p, age in old:
        with contextlib.suppress(OSError):
            if now - p.stat().st_mtime > age:
                p.unlink()
                n += 1
    return n


_ = dt
