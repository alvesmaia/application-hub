"""Excel spreadsheet import as a **durable job** (``ativos.import``).

Reading with **python-calamine** (``calamine`` reader); openpyxl only writes
(template, export, test spreadsheets). Before opening, the zip is inspected with the same
rules (entries, uncompressed size per item and total, compression ratio).

- The upload is written to ``/scratch`` and the job enters the queue in the same transaction as the audit and
  the idempotency marker (resubmission with the same ``Idempotency-Key`` returns the same job).
- The worker validates each row (types, required fields, lists, asset tag duplicated in the sheet) and
  writes batches of :data:`BATCH` rows with ``COPY``; each batch is written **in the same transaction** that
  advances the checkpoint (last row + counters). A crash midway → resume continues from there.
- Asset tags already registered go to the error report (CSV ``linha;coluna;valor;erro``,
  written after the batch commit).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
from hub_sdk import base, outbox
from hub_sdk.jobs import Job, JobCtx, JobFail, JobRetry, JobSpec
from hub_sdk.render import BOM, csv_line

from ativos.model import COLS, STATUSES, Invalid, map_headers, normalize
from ativos.xlsx_guard import check_zip

KIND = "ativos.import"
BATCH = 2000
# Error rows written to the report (the others are only counted).
MAX_ERROR_LINES = 100_000
SPEC = JobSpec(KIND, max_attempts=5, max_duration=3600)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Reading with python-calamine (calamine bindings). The
# calamine loads the whole sheet into NATIVE memory (outside the Python heap; measured: ~0.65
# KiB per 12-column row → ~130 MiB for 200k rows, roughly the uncompressed size of the sheet's XML)
# and `iter_rows()` hands the rows to Python one by one.
# So the cap on the uncompressed sheet is what limits the import's memory, and only ONE
# spreadsheet is loaded per process at a time (``ATIVOS_IMPORT_PARALLEL``).
MAX_ROWS = _env_int("HUB_IMPORT_MAX_ROWS", 300_000)
_LOAD_SLOTS = asyncio.Semaphore(max(1, _env_int("ATIVOS_IMPORT_PARALLEL", 1)))


class Validator:
    """Validates rows and keeps the asset tags seen (duplicates within the file)."""

    def __init__(self, headers: list[str]) -> None:
        try:
            self.map = map_headers(headers)
        except Invalid as e:
            raise JobFail(str(e)) from e
        self.tags: set[str] = set()

    def check(
        self, row: int, cells: list[Any] | tuple[Any, ...]
    ) -> tuple[list[Any] | None, list[tuple[int, str, str, str]]] | None:
        """``None`` = blank row; otherwise (values in COLS order | ``None``, errors)."""
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in cells):
            return None
        values, present, errs = self._normalize(row, cells)
        for ci, col in enumerate(COLS):
            if col.required and not present[ci]:
                errs.append((row, col.header, "", "campo obrigatório"))
        if not errs:
            self._check_tag(row, values[0] or "", errs)
        return (None if errs else values), errs

    def _normalize(
        self, row: int, cells: list[Any] | tuple[Any, ...]
    ) -> tuple[list[Any], list[bool], list[tuple[int, str, str, str]]]:
        """(values in COLS order, columns present in the sheet, conversion errors)."""
        values: list[Any] = [None] * len(COLS)
        present = [False] * len(COLS)
        errs: list[tuple[int, str, str, str]] = []
        for i, ci in enumerate(self.map):
            if ci is None:
                continue
            present[ci] = True
            raw = cells[i] if i < len(cells) else None
            try:
                values[ci] = normalize(COLS[ci], raw)
            except Invalid as e:
                errs.append((row, COLS[ci].header, raw_text(raw), str(e)))
        return values, present, errs

    def _check_tag(self, row: int, tag: str, errs: list[tuple[int, str, str, str]]) -> None:
        if tag in self.tags:
            errs.append((row, COLS[0].header, tag, "patrimônio duplicado na planilha"))
        else:
            self.tags.add(tag)


def raw_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class Reader:
    """First sheet of the ``.xlsx`` via python-calamine; rows delivered by ``iter_rows``
    (use from only one thread at a time)."""

    def __init__(self, path: Path) -> None:
        from python_calamine import CalamineWorkbook

        check_zip(path)
        try:
            self.wb = CalamineWorkbook.from_path(str(path))
            ws = self.wb.get_sheet_by_index(0)
        except Exception as e:
            raise JobFail(f"Arquivo .xlsx inválido: {e}") from e
        self.total = max(0, int(ws.height) - 1)
        self.rows: Iterator[list[Any]] = iter(ws.iter_rows())
        header = next(self.rows, None)
        if header is None:
            raise JobFail("A planilha está vazia.")
        self.validator = Validator([raw_text(h) for h in header])
        self.row_no = 1  # header

    def batch(
        self, n: int, skip_until: int = 0
    ) -> list[tuple[int, list[Any] | None, list[tuple[int, str, str, str]]]]:
        """Up to ``n`` new rows (after ``skip_until``) already validated:
        (row, values | ``None``, errors). Rows up to the checkpoint are validated again only
        so that the in-file duplicate check stays correct."""
        out: list[tuple[int, list[Any] | None, list[tuple[int, str, str, str]]]] = []
        for r in self.rows:
            self.row_no += 1
            item = self.validator.check(self.row_no, r)
            if item is None or self.row_no <= skip_until:
                continue
            out.append((self.row_no, item[0], item[1]))
            if len(out) >= n:
                break
        return out

    def close(self) -> None:
        self.rows = iter(())
        self.wb.close()


class Report:
    """CSV error report (``linha;coluna;valor;erro``), continued on a resume."""

    def __init__(self, path: Path, resume: bool) -> None:
        if resume and path.exists():
            self.f = path.open("a", encoding="utf-8", newline="")
        else:
            self.f = path.open("w", encoding="utf-8", newline="")
            self.f.write(BOM + csv_line(["linha", "coluna", "valor", "erro"]))

    def write(self, written: int, errs: list[tuple[int, str, str, str]]) -> int:
        for row, col, value, msg in errs:
            if written < MAX_ERROR_LINES:
                self.f.write(csv_line([str(row), col, value[:200], msg]))
            written += 1
        self.f.flush()
        return written

    def close(self) -> None:
        self.f.close()


def db_error_text(e: BaseException) -> str:
    return str(getattr(e, "message", None) or e)


@dataclass
class Progress:
    """Counters of an import, saved as the job checkpoint after each batch."""

    row: int
    inserted: int
    rejected: int
    written: int

    @classmethod
    def from_checkpoint(cls, cp: dict[str, Any]) -> Progress:
        return cls(
            int(cp.get("row", 0)),
            int(cp.get("inserted", 0)),
            int(cp.get("rejected", 0)),
            int(cp.get("error_lines", 0)),
        )

    def state(self, reader: Reader, error_lines: int) -> dict[str, int]:
        return {
            "row": self.row,
            "inserted": self.inserted,
            "rejected": self.rejected,
            "error_lines": error_lines,
            "read": reader.row_no - 1,
            "total": reader.total,
        }


async def _open(path: Path, report_path: Path, resume: bool) -> tuple[Reader, Report]:
    """Opens the sheet and the error report; an unexpected failure is retried, a bad file fails."""
    try:
        reader = await asyncio.to_thread(Reader, path)
    except JobFail:
        raise
    except Exception as e:
        raise JobRetry(f"falha ao abrir a planilha: {e}") from e
    try:
        report = await asyncio.to_thread(Report, report_path, resume)
    except OSError as e:
        reader.close()
        raise JobRetry(f"Não foi possível criar o relatório de erros: {e}") from e
    return reader, report


class ImportHandler:
    spec = SPEC

    async def run(self, ctx: JobCtx) -> Any:
        p = ctx.job.payload or {}
        path = Path(str(p.get("file_path", "")))
        if not await asyncio.to_thread(path.is_file):
            raise JobFail("O arquivo enviado não está mais disponível; envie a planilha novamente.")
        async with _LOAD_SLOTS:
            # Memory phase: if the jobs process is killed (OOM) with the sheet loaded,
            # the job becomes `failed` instead of retrying and killing the process again.
            with ctx.memory_phase():
                return await self._run(ctx, path, Path(str(p.get("report_path", ""))))

    async def _run(self, ctx: JobCtx, path: Path, report_path: Path) -> Any:
        ctx.raise_if_stopping()
        pr = Progress.from_checkpoint(dict(ctx.checkpoint or {}))
        reader, report = await _open(path, report_path, pr.row > 0)
        total = reader.total
        try:
            if total > MAX_ROWS:
                raise JobFail(f"A planilha tem {total} linhas; o limite é {MAX_ROWS}.")
            while await self._step(ctx, reader, report, pr):
                pass
        finally:
            await asyncio.to_thread(reader.close)
            await asyncio.to_thread(report.close)
        state = pr.state(reader, pr.written)
        async with ctx.pool.acquire() as c:
            await c.execute(
                "UPDATE hub_jobs SET progress_done = $3, progress_total = $4, checkpoint = $5 "
                "WHERE id = $1 AND attempt_token = $2 AND status = 'running'",
                ctx.job.id,
                ctx.job.attempt_token or "",
                state["read"],
                total,
                state,
            )
        with contextlib.suppress(OSError):
            await asyncio.to_thread(path.unlink)
        return state

    async def _step(self, ctx: JobCtx, reader: Reader, report: Report, pr: Progress) -> bool:
        """One batch: validate, ``COPY``, checkpoint (same transaction), then the error report.
        ``False`` when the sheet is over."""
        ctx.raise_if_stopping()
        batch = await asyncio.to_thread(reader.batch, BATCH, pr.row)
        if not batch:
            return False
        good = [(n, v) for n, v, e in batch if v is not None]
        errs = [e for _, _, es in batch for e in es]
        bad_rows = sum(1 for _, v, _e in batch if v is None)
        added, extra, _failed_copy = await self._write_batch(ctx, good)
        errs.extend(extra)
        pr.inserted += added
        pr.rejected += bad_rows + len(good) - added
        pr.row = max(pr.row, batch[-1][0])
        state = pr.state(reader, pr.written + len(errs))
        async with ctx.pool.acquire() as c, c.transaction():
            await ctx.save_checkpoint(c, state, state["read"], reader.total)
        pr.written = await asyncio.to_thread(report.write, pr.written, errs)
        return True

    async def _write_batch(
        self, ctx: JobCtx, good: list[tuple[int, list[Any]]]
    ) -> tuple[int, list[tuple[int, str, str, str]], bool]:
        """Writes the valid rows of the batch (``COPY``); returns (written, extra errors, COPY failed)."""
        if not good:
            return 0, [], False
        try:
            async with ctx.pool.acquire() as c, c.transaction():
                tags = [v[0] for _, v in good]
                existing = {
                    r["tag"] for r in await c.fetch("SELECT tag FROM assets WHERE tag = ANY($1)", tags)
                }
                errs: list[tuple[int, str, str, str]] = [
                    (n, COLS[0].header, v[0], "patrimônio já cadastrado") for n, v in good if v[0] in existing
                ]
                fresh = [tuple(v) for _, v in good if v[0] not in existing]
                if fresh:
                    await c.copy_records_to_table("assets", records=fresh, columns=[c_.key for c_ in COLS])
                return len(fresh), errs, False
        except (asyncpg.PostgresError, OSError) as e:
            # E.g. an asset tag inserted by someone else during the import — the whole batch
            # is rejected (the transaction was rolled back).
            first, last = good[0][0], good[-1][0]
            msg = f"lote não gravado: {db_error_text(e)}"
            return 0, [(first, "(lote)", f"linhas {first} a {last}", msg)], True

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None:
        name = str((job.payload or {}).get("file", ""))
        if status == "done":
            r = job.result if isinstance(job.result, dict) else (job.checkpoint or {})
            return outbox.audit(
                job.user,
                "Importação de ativos concluída",
                name,
                f"{r.get('inserted', 0)} gravadas, {r.get('rejected', 0)} rejeitadas",
            )
        action = "Importação de ativos cancelada" if status == "cancelled" else "Importação de ativos falhou"
        return outbox.audit(job.user, action, name, detail)

    def final_events(self, job: Job, status: str, detail: str) -> list[outbox.Event]:
        """Wave 9: notice in the Inbox of whoever imported (same transaction as the final state)."""
        p = job.payload or {}
        name, login = str(p.get("file", "")), str(p.get("login", ""))
        title, level = {
            "done": ("Importação de ativos concluída", "success"),
            "cancelled": ("Importação de ativos cancelada", "warning"),
        }.get(status, ("Importação de ativos falhou", "error"))
        if status == "done":
            r = job.result if isinstance(job.result, dict) else (job.checkpoint or {})
            body = f"{name}: {r.get('inserted', 0)} gravadas, {r.get('rejected', 0)} rejeitadas"
        else:
            body = f"{name}: {detail}"
        return [
            outbox.notification(
                job.user,
                title,
                body=body,
                link=f"{base('ativos')}/assets?import={job.id}",
                to_users=[login] if login else [],
                level=level,
            )
        ]

    def after_final(self, job: Job, status: str) -> None:
        """Terminal state: deletes the upload from ``/scratch`` (reprocessing asks for the spreadsheet again)."""
        p = str((job.payload or {}).get("file_path", ""))
        if p and Path(p).name.startswith("upload-"):
            with contextlib.suppress(OSError):
                Path(p).unlink()


def job_view(j: Job) -> dict[str, Any]:
    """The job as the screen shows it (checkpoint, result and state)."""
    p = j.payload or {}
    cp = j.result if (j.status == "done" and isinstance(j.result, dict)) else (j.checkpoint or {})
    return {
        "id": j.id,
        "state": j.status,
        "file": str(p.get("file", "")),
        "report": str(p.get("report_path", "")),
        "total": int(cp.get("total", 0)),
        "read": int(cp.get("read", 0)),
        "inserted": int(cp.get("inserted", 0)),
        "rejected": int(cp.get("rejected", 0)),
        "error_lines": int(cp.get("error_lines", 0)),
        "checkpoint": int(cp.get("row", 0)),
        "attempts": j.attempts,
        "max_attempts": j.max_attempts,
        "elapsed": j.elapsed_s,
        "error": j.error or "",
    }


def template_xlsx(path: Path) -> None:
    """Template with the headers and 3 sample rows."""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Ativos")
    ws.append([c.header for c in COLS])
    ex = [
        {"tag": "EX-0001", "name": "Notebook Dell Latitude 5440", "category": "Tecnologia", "location": "São Paulo",
         "status": "Em uso", "brand": "Dell", "model": "Latitude 5440", "responsible": "Mariana Costa",
         "acquisition_date": "15/03/2024", "acquisition_value_cents": "7890,00", "condition": "Novo"},
        {"tag": "EX-0002", "name": "Cadeira ergonômica", "category": "Mobiliário", "location": "Curitiba",
         "status": "Disponível", "brand": "Cadence", "acquisition_date": "02/05/2023",
         "acquisition_value_cents": "1250,50", "condition": "Bom"},
        {"tag": "EX-0003", "name": "Projetor Epson PowerLite", "category": "Audiovisual", "location": "Recife",
         "status": "Manutenção", "brand": "Epson", "acquisition_date": "20/11/2022",
         "acquisition_value_cents": "3499,90", "condition": "Regular"},
    ]  # fmt: skip
    for e in ex:
        ws.append([e.get(c.key, "") for c in COLS])
    wb.save(path)


def generate_xlsx(path: Path, rows: int, bad_per_mille: int = 10, prefix: str = "IMP-") -> None:
    """Sample spreadsheet for the import (tests, chaos, seed)."""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Ativos")
    ws.append([c.header for c in COLS])
    cats = ("Tecnologia", "Mobiliário", "Veículos", "Audiovisual", "Telefonia", "Ferramentas")
    locs = ("São Paulo", "Rio de Janeiro", "Curitiba", "Belo Horizonte", "Recife", "Remoto")
    for i in range(1, rows + 1):
        bad = bad_per_mille > 0 and (i * 7919) % 1000 < bad_per_mille
        ws.append(
            [
                f"{prefix}{i:07d}",
                f"Ativo importado {i}",
                cats[i % 6],
                locs[i % 6],
                "Status inválido" if bad else STATUSES[i % len(STATUSES)],  # the generator uses all statuses
                "Marca X",
                f"Modelo {i % 50}",
                f"SN{i:010d}",
                "",
                "",
                f"CC-{1000 + i % 9000}",
                "Tecnologia",
                "Importação",
                "",
                "",
                f"{1 + i % 28:02d}/01/2025",
                f"{1000 + i % 9000},{i % 100:02d}",
                "",
                "",
                "",
                "",
                "Bom",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
    wb.save(path)


def count_xlsx(path: Path) -> dict[str, int]:
    """Validates the spreadsheet the way the import would (without DB)."""
    r = Reader(path)
    valid = rejected = 0
    try:
        while True:
            b = r.batch(BATCH)
            if not b:
                break
            bad = sum(1 for _, v, _e in b if v is None)
            rejected += bad
            valid += len(b) - bad
    finally:
        r.close()
    return {"total": r.total, "valid": valid, "rejected": rejected}
