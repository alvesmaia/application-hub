"""Shared pieces of :mod:`hub_sdk.storage`: errors, items, path rules, streaming downloads, the HTTP
transport and the :class:`Connection` interface every backend implements."""

from __future__ import annotations

import asyncio
import builtins
import http.client
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import IO, Any, cast
from urllib.parse import urlsplit

_LOOKALIKES = set("․﹒．∕⁄／⧸＼⧹﹨​‎‏‮﻿")
_BAD = set('\\%:*?"<>|#')
CHUNK = 64 * 1024


class StorageError(Exception):
    """Storage failure: HTTP ``status`` (400 path, 403 operation not allowed,
    404 missing or connection not linked, 409, 413, 429/503 transient, 502 provider)."""

    def __init__(self, status: int, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        return self.status in (429, 503)


@dataclass(frozen=True)
class Item:
    path: str
    name: str
    size: int = 0
    modified: str = ""
    folder: bool = False
    content_type: str | None = None
    etag: str | None = None

    @staticmethod
    def of(d: dict[str, Any]) -> Item:
        return Item(
            path=str(d.get("path", "")),
            name=str(d.get("name", "")),
            size=int(d.get("size") or 0),
            modified=str(d.get("modified") or ""),
            folder=bool(d.get("folder")),
            content_type=d.get("content_type"),
            etag=d.get("etag"),
        )


@dataclass(frozen=True)
class ShareLink:
    url: str
    kind: str
    expires: str | None = None


def _check_segment(seg: str) -> None:
    if seg in (".", ".."):
        raise StorageError(400, 'Caminho inválido: ".." e "." não são permitidos.')
    if len(seg.encode()) > 255:
        raise StorageError(400, "Caminho inválido: nome longo demais.")
    if seg[0] == " " or seg[-1] in " .":
        raise StorageError(400, "Caminho inválido: espaço ou ponto nas pontas do nome.")
    for ch in seg:
        if ord(ch) < 32 or ord(ch) == 127 or (0x80 <= ord(ch) < 0xA0):
            raise StorageError(400, "Caminho inválido: caractere de controle.")
        if ch in _BAD or ch in _LOOKALIKES:
            raise StorageError(400, "Caminho inválido: caractere não permitido.")


def clean_path(p: str, file: bool = True) -> str:
    """Normalizes a path relative to the base folder (same rules as the hub): no ``..``/``.``,
    ``\\``, ``%``, ``:*?"<>|#``, control characters, Unicode look-alikes of ``.``/``/``; segments without
    leading space nor trailing space/dot in the segment; ≤ 255 bytes per segment, ≤ 32 levels,
    ≤ 1024 bytes in total."""
    if len(p.encode()) > 1024:
        raise StorageError(400, "Caminho longo demais.")
    segs = [seg for seg in p.split("/") if seg]
    for seg in segs:
        _check_segment(seg)
    if len(segs) > 32:
        raise StorageError(400, "Pastas aninhadas demais.")
    out = "/".join(segs)
    if file and not out:
        raise StorageError(400, "Informe o caminho do arquivo.")
    return out


class Download:
    """Streaming body: ``async for chunk in d.chunks()``; ``aclose()`` releases the connection."""

    def __init__(self, content_type: str, length: int | None, resp: Any, conn: Any) -> None:
        self.content_type = content_type
        self.length = length
        self._resp = resp
        self._conn = conn

    async def chunks(self) -> AsyncIterator[bytes]:
        try:
            while True:
                b = await asyncio.to_thread(self._resp.read, CHUNK)
                if not b:
                    break
                yield cast(bytes, b)
        finally:
            await self.aclose()

    async def read(self) -> bytes:
        return b"".join([c async for c in self.chunks()])

    async def aclose(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)


Body = bytes | IO[bytes]


def _http(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    u = urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.hostname or "", u.port, timeout=timeout)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return conn, path


def _send(
    method: str, url: str, headers: dict[str, str], body: Body | None, timeout: float
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    conn, path = _http(url, timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        return conn, conn.getresponse()
    except Exception:
        conn.close()
        raise


async def _call(
    method: str, url: str, headers: dict[str, str], body: Body | None = None, timeout: float = 120.0
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    try:
        return await asyncio.to_thread(_send, method, url, headers, body, timeout)
    except OSError as e:
        raise StorageError(503, f"armazenamento inalcançável ({type(e).__name__})", 2) from e


def _retry_after(resp: http.client.HTTPResponse) -> int | None:
    v = resp.getheader("retry-after")
    return int(v) if v is not None and v.strip().isdigit() else None


class Connection:
    """A storage connection (use :func:`connection` or :func:`s3_direct`)."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.user: str | None = None
        self.user_sig: str | None = None

    @property
    def via_broker(self) -> bool:
        return False

    def for_user(self, x_hub_user: str | None, x_hub_user_sig: str | None = None) -> Connection:
        """Same connection, recording the request's user on the hub.

        Forward the ``x-hub-user`` **and** the ``x-hub-user-sig`` (signature the gateway
        injected): only with the signature does the hub record the user as the author; without it, the author
        is the app and the user shows as "informado pelo app (não verificado)".
        """
        c = self._copy()
        c.user = x_hub_user or None
        c.user_sig = (x_hub_user_sig or None) if c.user is not None and c.user else None
        return c

    def _copy(self) -> Connection:  # pragma: no cover — overridden
        raise NotImplementedError

    async def upload(self, path: str, data: Body, content_type: str, length: int | None = None) -> Item:
        raise NotImplementedError

    async def download(self, path: str) -> Download:
        raise NotImplementedError

    async def list(self, prefix: str = "") -> builtins.list[Item]:
        raise NotImplementedError

    async def meta(self, path: str) -> Item:
        raise NotImplementedError

    async def delete(self, path: str) -> None:
        raise NotImplementedError

    async def move(self, src: str, dst: str) -> Item:
        raise NotImplementedError

    async def share_link(self, path: str, kind: str = "view", expires_hours: int = 24) -> ShareLink:
        raise NotImplementedError


def _length(data: Body, length: int | None) -> int:
    if isinstance(data, bytes | bytearray):
        return len(data)
    if length is None:
        pos = data.tell()
        data.seek(0, os.SEEK_END)
        length = data.tell() - pos
        data.seek(pos)
    return length


def _error(status: int, raw: bytes, retry_after: int | None) -> StorageError:
    try:
        msg = str(json.loads(raw).get("error") or "falha no armazenamento")
    except (ValueError, AttributeError):
        msg = "falha no armazenamento"
    if status in (429, 503) and retry_after is None:
        retry_after = 2
    return StorageError(status, msg, retry_after)
