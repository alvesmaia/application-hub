"""Importação de planilha Excel como **tarefa durável** (``ativos.import``).

Leitura com **python-calamine** (leitor ``calamine``); o openpyxl só escreve
(modelo, exportação, planilhas de teste). Antes de abrir, o zip é inspecionado com as mesmas
regras (entradas, tamanho descomprimido por item e total, razão de compressão).

- O upload é gravado em ``/scratch`` e a tarefa entra na fila na mesma transação da auditoria e
  da marca de idempotência (reenvio com a mesma ``Idempotency-Key`` devolve a mesma tarefa).
- O worker valida cada linha (tipos, obrigatórios, listas, patrimônio duplicado na planilha) e
  grava lotes de :data:`BATCH` linhas com ``COPY``; cada lote é gravado **na mesma transação** que
  avança o checkpoint (última linha + contadores). Queda no meio → a retomada continua dali.
- Patrimônios já cadastrados vão para o relatório de erros (CSV ``linha;coluna;valor;erro``,
  gravado depois do commit do lote).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
from hub_sdk import outbox
from hub_sdk.jobs import Job, JobCtx, JobFail, JobRetry, JobSpec
from hub_sdk.render import BOM, csv_line

from ativos.model import COLS, STATUSES, Invalid, map_headers, normalize

KIND = "ativos.import"
BATCH = 2000
# Linhas de erro gravadas no relatório (as demais só são contadas).
MAX_ERROR_LINES = 100_000
SPEC = JobSpec(KIND, max_attempts=5, max_duration=3600)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Leitura com python-calamine (bindings do calamine). O
# calamine carrega a aba inteira em memória NATIVA (fora do heap Python; medido: ~0,65
# KiB por linha de 12 colunas → ~130 MiB para 200 mil linhas, praticamente o tamanho
# descomprimido do XML da aba) e o `iter_rows()` entrega as linhas ao Python uma a uma.
# Por isso o teto da aba descomprimida é o que limita a memória da importação, e só UMA
# planilha é carregada por processo por vez (``ATIVOS_IMPORT_PARALLEL``).
MAX_ROWS = _env_int("HUB_IMPORT_MAX_ROWS", 300_000)
# Regras do zip (diretório central, sem descomprimir nada):
MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_TOTAL_UNCOMPRESSED = 2560 * 1024 * 1024
MAX_ZIP_ENTRY_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_RATIO = 100
# Limitação atual: a aba inteira vira memória (sem leitura em streaming), então o teto é
# menor (padrão 300 MiB ≈ 460 mil linhas simples).
MAX_ZIP_SHEET_UNCOMPRESSED = _env_int("ATIVOS_IMPORT_MAX_SHEET_MB", 300) * 1024 * 1024
_LOAD_SLOTS = asyncio.Semaphore(max(1, _env_int("ATIVOS_IMPORT_PARALLEL", 1)))


def check_zip_entries(entries: list[tuple[str, int, int]]) -> None:
    """Regras sobre (nome, comprimido, descomprimido) de cada entrada."""
    if len(entries) > MAX_ZIP_ENTRIES:
        raise JobFail(
            f"Arquivo .xlsx inválido: {len(entries)} itens no arquivo compactado (limite {MAX_ZIP_ENTRIES})."
        )
    total = 0
    for name, compressed, uncompressed in entries:
        is_sheet = name.startswith("xl/worksheets/") and name.endswith(".xml")
        entry_max = MAX_ZIP_SHEET_UNCOMPRESSED if is_sheet else MAX_ZIP_ENTRY_UNCOMPRESSED
        if uncompressed > entry_max:
            raise JobFail(
                f"Arquivo .xlsx inválido: um item do arquivo descomprime para mais de {entry_max >> 20} MB."
            )
        total += uncompressed
        if total > MAX_ZIP_TOTAL_UNCOMPRESSED:
            raise JobFail(
                f"Arquivo .xlsx inválido: o conteúdo descomprimido passa de {MAX_ZIP_TOTAL_UNCOMPRESSED >> 20} MB."
            )
        if (
            compressed > 0
            and uncompressed > 1024 * 1024
            and uncompressed // max(compressed, 1) > MAX_ZIP_RATIO
        ):
            raise JobFail(
                "Arquivo .xlsx inválido: taxa de compressão suspeita em um item do arquivo "
                "(possível arquivo malicioso)."
            )
    if not any(n.startswith("xl/worksheets/") for n, _, _ in entries):
        raise JobFail("O arquivo não tem nenhuma planilha.")


INVALID = "O arquivo não é uma planilha .xlsx válida."
_LOCAL = struct.Struct("<4s5H3L2H")  # cabeçalho local do zip (30 bytes)
_INFLATE_CHUNK = 256 * 1024


def _inflated_size(f: Any, method: int, compressed: int, cap: int) -> int:
    """Tamanho REAL descomprimido de uma entrada (lendo os bytes, não o que o zip declara),
    parando assim que passar de ``cap`` (devolve ``cap + 1``). Memória constante."""
    if method == zipfile.ZIP_STORED:
        return compressed
    if method != zipfile.ZIP_DEFLATED:
        raise JobFail("Arquivo .xlsx inválido: método de compressão não suportado.")
    d = zlib.decompressobj(-15)
    left = compressed
    total = 0
    while left > 0 and not d.eof:
        chunk = f.read(min(_INFLATE_CHUNK, left))
        if not chunk:
            break
        left -= len(chunk)
        data = chunk
        while data and not d.eof:  # ty: ignore[redundant-condition-strict]  # typeshed fixa `eof` em False; muda ao fim do fluxo
            out = d.decompress(data, _INFLATE_CHUNK)
            total += len(out)
            if total > cap:
                return cap + 1
            data = d.unconsumed_tail
    return total


def actual_entries(path: Path) -> list[tuple[str, int, int]]:
    """(nome, comprimido, descomprimido REAL) de cada entrada. O diretório central (e o
    cabeçalho local) são do autor do arquivo: um zip pode declarar 1 KB e descomprimir para
    gigabytes. Aqui cada entrada é descomprimida em streaming e contada, com teto — o que o
    calamine vai ler depois é exatamente isto."""
    try:
        with zipfile.ZipFile(path) as z, path.open("rb") as f:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                check_zip_entries([(i.filename, 0, 0) for i in infos])
            out: list[tuple[str, int, int]] = []
            total = 0
            for i in infos:
                is_sheet = i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml")
                entry_max = MAX_ZIP_SHEET_UNCOMPRESSED if is_sheet else MAX_ZIP_ENTRY_UNCOMPRESSED
                cap = max(0, min(entry_max, MAX_ZIP_TOTAL_UNCOMPRESSED - total))
                f.seek(i.header_offset)
                h = f.read(_LOCAL.size)
                if len(h) < _LOCAL.size or h[:4] != b"PK\x03\x04":
                    raise JobFail(INVALID)
                name_len, extra_len = _LOCAL.unpack(h)[-2:]
                f.seek(i.header_offset + _LOCAL.size + name_len + extra_len)
                real = _inflated_size(f, i.compress_type, i.compress_size, cap)
                out.append((i.filename, i.compress_size, max(real, i.file_size)))
                total += real
                if real > cap:
                    break  # check_zip_entries recusa com a mensagem certa
            return out
    except (zipfile.BadZipFile, zlib.error, OSError, struct.error, EOFError) as e:
        raise JobFail(INVALID) from e


def check_zip(path: Path) -> None:
    """Antes de entregar o arquivo ao calamine: regras sobre o diretório central
    (rápido) e depois sobre os tamanhos REAIS descomprimidos (:func:`actual_entries`)."""
    try:
        with zipfile.ZipFile(path) as z:
            entries = [(i.filename, i.compress_size, i.file_size) for i in z.infolist()]
    except (zipfile.BadZipFile, OSError) as e:
        raise JobFail(INVALID) from e
    check_zip_entries(entries)
    check_zip_entries(actual_entries(path))


class Validator:
    """Valida linhas e guarda os patrimônios vistos (duplicados dentro do arquivo)."""

    def __init__(self, headers: list[str]) -> None:
        try:
            self.map = map_headers(headers)
        except Invalid as e:
            raise JobFail(str(e)) from e
        self.tags: set[str] = set()

    def check(
        self, row: int, cells: list[Any] | tuple[Any, ...]
    ) -> tuple[list[Any] | None, list[tuple[int, str, str, str]]] | None:
        """``None`` = linha em branco; senão (valores na ordem de COLS | ``None``, erros)."""
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in cells):
            return None
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
        for ci, col in enumerate(COLS):
            if col.required and not present[ci]:
                errs.append((row, col.header, "", "campo obrigatório"))
        if not errs:
            tag = values[0] or ""
            if tag in self.tags:
                errs.append((row, COLS[0].header, tag, "patrimônio duplicado na planilha"))
            else:
                self.tags.add(tag)
        return (None if errs else values), errs


def raw_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class Reader:
    """Primeira aba do ``.xlsx`` via python-calamine; linhas entregues por ``iter_rows``
    (usar só de uma thread por vez)."""

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
        self.row_no = 1  # cabeçalho

    def batch(
        self, n: int, skip_until: int = 0
    ) -> list[tuple[int, list[Any] | None, list[tuple[int, str, str, str]]]]:
        """Até ``n`` linhas novas (depois de ``skip_until``) já validadas:
        (linha, valores | ``None``, erros). As linhas até o checkpoint são validadas de novo só
        para o controle de duplicados dentro do arquivo continuar correto."""
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
    """Relatório de erros CSV (``linha;coluna;valor;erro``), continuado numa retomada."""

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


class ImportHandler:
    spec = SPEC

    async def run(self, ctx: JobCtx) -> Any:
        p = ctx.job.payload or {}
        path = Path(str(p.get("file_path", "")))
        if not await asyncio.to_thread(path.is_file):
            raise JobFail("O arquivo enviado não está mais disponível; envie a planilha novamente.")
        async with _LOAD_SLOTS:
            # Fase de memória: se o processo de tarefas for morto (OOM) com a aba carregada,
            # a tarefa vira `failed` em vez de repetir e matar o processo de novo.
            with ctx.memory_phase():
                return await self._run(ctx, path, Path(str(p.get("report_path", ""))))

    async def _run(self, ctx: JobCtx, path: Path, report_path: Path) -> Any:
        ctx.raise_if_stopping()
        cp = dict(ctx.checkpoint or {})
        row = int(cp.get("row", 0))
        inserted, rejected = int(cp.get("inserted", 0)), int(cp.get("rejected", 0))
        written = int(cp.get("error_lines", 0))
        try:
            reader = await asyncio.to_thread(Reader, path)
        except JobFail:
            raise
        except Exception as e:
            raise JobRetry(f"falha ao abrir a planilha: {e}") from e
        try:
            report = await asyncio.to_thread(Report, report_path, row > 0)
        except OSError as e:
            reader.close()
            raise JobRetry(f"Não foi possível criar o relatório de erros: {e}") from e
        total = reader.total
        try:
            if total > MAX_ROWS:
                raise JobFail(f"A planilha tem {total} linhas; o limite é {MAX_ROWS}.")
            while True:
                ctx.raise_if_stopping()
                batch = await asyncio.to_thread(reader.batch, BATCH, row)
                if not batch:
                    break
                good = [(n, v) for n, v, e in batch if v is not None]
                errs = [e for _, _, es in batch for e in es]
                bad_rows = sum(1 for _, v, _e in batch if v is None)
                added, extra, failed_copy = await self._write_batch(ctx, good)
                errs.extend(extra)
                rejected_now = bad_rows + len(good) - added
                inserted += added
                rejected += rejected_now
                row = max(row, batch[-1][0])
                state = {
                    "row": row,
                    "inserted": inserted,
                    "rejected": rejected,
                    "error_lines": written + len(errs),
                    "read": reader.row_no - 1,
                    "total": total,
                }
                async with ctx.pool.acquire() as c, c.transaction():
                    await ctx.save_checkpoint(c, state, state["read"], total)
                _ = failed_copy
                written = await asyncio.to_thread(report.write, written, errs)
        finally:
            await asyncio.to_thread(reader.close)
            await asyncio.to_thread(report.close)
        state = {
            "row": row,
            "inserted": inserted,
            "rejected": rejected,
            "error_lines": written,
            "read": reader.row_no - 1,
            "total": total,
        }
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

    async def _write_batch(
        self, ctx: JobCtx, good: list[tuple[int, list[Any]]]
    ) -> tuple[int, list[tuple[int, str, str, str]], bool]:
        """Grava os válidos do lote (``COPY``); devolve (gravados, erros extras, falhou o COPY)."""
        if not good:
            return 0, [], False
        errs: list[tuple[int, str, str, str]] = []
        try:
            async with ctx.pool.acquire() as c, c.transaction():
                tags = [v[0] for _, v in good]
                existing = {
                    r["tag"] for r in await c.fetch("SELECT tag FROM assets WHERE tag = ANY($1)", tags)
                }
                fresh = []
                for n, v in good:
                    if v[0] in existing:
                        errs.append((n, COLS[0].header, v[0], "patrimônio já cadastrado"))
                    else:
                        fresh.append(tuple(v))
                if fresh:
                    await c.copy_records_to_table("assets", records=fresh, columns=[c_.key for c_ in COLS])
                return len(fresh), errs, False
        except (asyncpg.PostgresError, OSError) as e:
            # Ex.: patrimônio inserido por outra pessoa durante a importação — o lote inteiro
            # vira rejeitado (a transação foi desfeita).
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
        """Onda 9: aviso na Inbox de quem importou (mesma transação do estado final)."""
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
                link=f"/apps/ativos/assets?import={job.id}",
                to_users=[login] if login else [],
                level=level,
            )
        ]

    def after_final(self, job: Job, status: str) -> None:
        """Estado terminal: apaga o upload do ``/scratch`` (reprocessar pede a planilha de novo)."""
        p = str((job.payload or {}).get("file_path", ""))
        if p and Path(p).name.startswith("upload-"):
            with contextlib.suppress(OSError):
                Path(p).unlink()


def job_view(j: Job) -> dict[str, Any]:
    """A tarefa como a tela mostra (checkpoint, resultado e estado)."""
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
    """Modelo com os cabeçalhos e 3 linhas de exemplo."""
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
    """Planilha de exemplo para a importação (testes, caos, carga)."""
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
                "Status inválido" if bad else STATUSES[i % len(STATUSES)],  # o gerador usa todos os status
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
    """Valida a planilha como a importação faria (sem banco)."""
    r = Reader(path)
    valid = rejected = 0
    try:
        while True:
            b = r.batch(BATCH)
            if not b:
                break
            for _, v, _e in b:
                if v is None:
                    rejected += 1
                else:
                    valid += 1
    finally:
        r.close()
    return {"total": r.total, "valid": valid, "rejected": rejected}
