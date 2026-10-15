"""Attachment upload: size/type checks while spooling to the scratch dir, then the store."""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from fastapi import Request
from hub_sdk import storage
from starlette.datastructures import UploadFile

from fornecedores.state import State
from fornecedores.suppliers import Supplier, attach_max_bytes, check_type, safe_name
from fornecedores.views import storage_status


class UploadRefused(Exception):
    """Upload not stored: the status, notice title/text and optional ``Retry-After`` to answer with."""

    def __init__(self, status: int, title: str, text: str, retry: int | None = None) -> None:
        super().__init__(text)
        self.status, self.title, self.text, self.retry = status, title, text, retry


def _too_big(limit: int) -> UploadRefused:
    return UploadRefused(413, "Arquivo muito grande", f"O limite para anexos é {limit // 1024 // 1024} MB.")


def _no_workspace(e: OSError) -> UploadRefused:
    return UploadRefused(
        503, "Espaço de trabalho indisponível", f"Nada foi guardado; tente de novo em instantes ({e}).", 2
    )


def _declared_length(req: Request) -> int:
    try:
        return int(req.headers.get("content-length") or 0)
    except ValueError:
        return 0


async def _file_field(req: Request) -> UploadFile:
    try:
        form = await req.form(max_files=1, max_fields=10)
    except Exception as e:
        raise UploadRefused(400, "Envio interrompido", "O arquivo não pôde ser lido.") from e
    up = form.get("file")
    if not isinstance(up, UploadFile):
        raise UploadRefused(422, "Nenhum arquivo enviado", "Selecione um PDF ou imagem.")
    return up


async def _spool_checked(up: UploadFile, tmp: Path, name: str, limit: int) -> tuple[str, int]:
    try:
        return await _spool(up, tmp, name, limit)
    except ValueError as e:
        raise UploadRefused(415, "Arquivo recusado", str(e)) from e
    except OverflowError as e:
        raise _too_big(limit) from e
    except OSError as e:
        raise _no_workspace(e) from e
    finally:
        await up.close()


async def _put(store: storage.Connection, key: str, tmp: Path, ct: str, size: int) -> None:
    try:
        with tmp.open("rb") as f:
            await store.upload(key, f, ct, size)
    except storage.StorageError as e:
        status, retry = storage_status(e)
        raise UploadRefused(status, "Não foi possível guardar o anexo", e.message, retry) from e
    except OSError as e:
        raise _no_workspace(e) from e


async def receive(st: State, sup: Supplier, req: Request) -> tuple[str, str, int]:
    """Stores the uploaded file under ``{code}/{timestamp}-{name}``; returns (name, content-type, bytes)."""
    store = st.user_store(req)
    if store is None:
        raise UploadRefused(503, "Object store não configurado", "Não é possível receber anexos agora.")
    limit = attach_max_bytes()
    if _declared_length(req) > limit + 1024 * 1024:
        raise _too_big(limit)
    up = await _file_field(req)
    name = up.filename or "arquivo"
    tmp = st.scratch / f"anexo-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    ct, size = await _spool_checked(up, tmp, name, limit)
    try:
        key = f"{sup.code}/{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{safe_name(name)}"
        await _put(store, key, tmp, ct, size)
    finally:
        tmp.unlink(missing_ok=True)
    return name, ct, size


async def _spool(up: UploadFile, dest: Path, name: str, limit: int) -> tuple[str, int]:
    """Writes the upload to ``dest``, checking type (extension, content-type and signature) in the
    first bytes and the size limit; returns (content-type, bytes). On failure removes ``dest``."""
    size, head, ct = 0, b"", ""
    declared = up.content_type or ""
    out = await asyncio.to_thread(partial(dest.open, "wb"))
    try:
        while chunk := await up.read(1024 * 1024):
            if not ct:
                head += chunk[: max(0, 16 - len(head))]
                ct = check_type(name, declared, head) if len(head) >= 12 else ""
            size += len(chunk)
            if size > limit:
                raise OverflowError
            await asyncio.to_thread(out.write, chunk)
        ct = ct or check_type(name, declared, head)
    except BaseException:
        await asyncio.to_thread(out.close)
        await asyncio.to_thread(partial(dest.unlink, missing_ok=True))
        raise
    await asyncio.to_thread(out.close)
    return ct, size
