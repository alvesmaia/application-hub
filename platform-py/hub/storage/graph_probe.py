"""Connection test of a SharePoint connection: token, site, library, base folder and a write/read/delete probe."""

from __future__ import annotations

import random
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from hub.storage import Body, Steps, StorageError

if TYPE_CHECKING:
    from hub.storage.graph import GraphClient


async def probe(g: GraphClient) -> list[dict[str, Any]]:
    s = Steps()
    g._token = g._target = None
    did = await _locate(g, s)
    base = g.cfg.base_folder

    async def folder() -> tuple[None, str]:
        await g._ensure_folder(did, base)
        return None, f'Pasta-base "{base}" pronta.' if base else "Raiz da biblioteca."

    await s.run("folder", folder)
    await _write_read_delete(g, s)
    return s.steps


async def _locate(g: GraphClient, s: Steps) -> str:
    """Token, site and library steps; returns the drive id ("" when a step failed)."""

    async def token() -> tuple[None, str]:
        await g.token()
        return None, "Token app-only obtido (segredo do cliente)."

    async def site() -> tuple[tuple[str, str], str]:
        sid, name = await g._resolve_site()
        return (sid, name), f'Site "{name}".'

    await s.run("token", token)
    sid, site_name = await s.run("site", site) or ("", "")

    async def drive() -> tuple[str, str]:
        did, name = await g._resolve_drive(sid)
        g._target = (did, site_name, name)
        return did, f'Biblioteca "{name}".'

    return cast(str, await s.run("drive", drive) or "")


async def _write_read_delete(g: GraphClient, s: Steps) -> None:  # noqa: PLR0915
    probe_name = f".hub-sonda-{random.randrange(16**8):08x}.txt"  # noqa: S311
    data = bytes(random.choices(b"abcdefghijklmnopqrstuvwxyz0123456789", k=64))  # noqa: S311

    async def feed() -> AsyncIterator[bytes]:
        yield data

    async def write() -> tuple[None, str]:
        await g.upload(probe_name, len(data), "text/plain", Body(feed()))
        return None, f"Arquivo de sonda {probe_name} gravado ({len(data)} bytes)."

    async def read() -> tuple[None, str]:
        _, _, stream = await g.download(probe_name)
        if b"".join([c async for c in stream]) != data:
            raise StorageError("upstream", "O conteúdo lido difere do gravado.")
        return None, "Conteúdo lido e conferido."

    wrote = await s.run("write", write)
    await s.run("read", read)
    if not wrote:
        s.steps.append({"key": "delete", "ok": None, "detail": "", "ms": 0})
        return
    t0 = time.monotonic()
    try:
        await g.delete(probe_name)
        ms = int((time.monotonic() - t0) * 1000)
        s.steps.append({"key": "delete", "ok": True, "detail": "Arquivo de sonda apagado.", "ms": ms})
    except StorageError as e:
        s.steps.append({"key": "delete", "ok": False, "detail": e.message, "ms": 0})
