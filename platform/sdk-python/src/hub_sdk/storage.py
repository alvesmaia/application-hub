"""App file storage (wave 10, contract item 22): platform connections through the **hub broker** (SharePoint, S3) or the
app's own bucket (**direct S3**, ``HUB_S3_*``). Switching backend is configuration
(binding in Administration › Connections), not code.

.. code-block:: python

    docs = storage.connection("contratos-docs")      # None if not linked
    if docs:
        docs = docs.for_user(req.headers.get("x-hub-user"), req.headers.get("x-hub-user-sig"))
        await docs.upload("2026/CT-1.pdf", upload.file, "application/pdf", length=n)
        d = await docs.download("2026/CT-1.pdf")     # d.chunks(): async iterator

- **Broker** (``HUB_CONNECTIONS`` lists the connection; ``HUB_STORAGE_URL`` + ``HUB_STORAGE_TOKEN``):
  the hub keeps the credential and runs the operation confined to the connection's base folder; the app
  never sees the client secret nor a Graph token.
- **Direct S3** (:func:`s3_direct`): SigV4 against the app's bucket.

No new dependencies: HTTP with ``http.client`` in ``asyncio.to_thread`` (upload from a
file/bytes without loading everything into memory; download in chunks). Paths validated here with
the same rules as the hub (:func:`clean_path`).
"""

from __future__ import annotations

import asyncio
import builtins
import datetime as dt
import json
import os
from typing import Any
from urllib.parse import quote

from typing_extensions import override

from hub_sdk.storage_core import CHUNK as CHUNK
from hub_sdk.storage_core import (
    Body,
    Connection,
    Download,
    Item,
    ShareLink,
    StorageError,
    _call,
    _error,
    _length,
    _retry_after,
    clean_path,
)
from hub_sdk.storage_s3 import S3Config, S3Connection

__all__ = [
    "Connection",
    "Download",
    "Item",
    "LocalConnection",
    "S3Config",
    "ShareLink",
    "StorageError",
    "bound",
    "clean_path",
    "connection",
    "s3_direct",
]


class BrokerConnection(Connection):
    """Through the hub's broker (app's internal network, token per app × environment)."""

    def __init__(self, name: str, kind: str, base: str, token: str) -> None:
        super().__init__(name, kind)
        self.base = base.rstrip("/")
        self._token = token

    @override
    def __repr__(self) -> str:
        return f"BrokerConnection({self.name!r}, {self.kind!r})"

    @property
    @override
    def via_broker(self) -> bool:
        return True

    @override
    def _copy(self) -> Connection:
        return BrokerConnection(self.name, self.kind, self.base, self._token)

    def _url(self, op: str, **q: str) -> str:
        qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in q.items())
        return f"{self.base}/_hub/storage/{quote(self.name, safe='')}/{op}" + (f"?{qs}" if qs else "")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h: dict[str, str] = {"Authorization": f"Bearer {self._token}"}
        if self.user is not None and self.user:
            h["x-hub-user"] = self.user
            if self.user_sig is not None and self.user_sig:
                h["x-hub-user-sig"] = self.user_sig
        h.update(extra or {})
        return h

    async def _json(self, method: str, url: str, body: bytes | None = None, retry: bool = True) -> Any:
        extra = {"Content-Type": "application/json"} if body is not None else {}
        for attempt in range(1, 4):
            try:
                conn, resp = await _call(method, url, self._headers(extra), body, timeout=60.0)
            except StorageError:
                if retry and attempt < 3:
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise
            try:
                raw = await asyncio.to_thread(resp.read)
            finally:
                conn.close()
            if 200 <= resp.status < 300:
                return json.loads(raw) if raw else None
            err = _error(resp.status, raw, _retry_after(resp))
            if retry and err.transient and attempt < 3 and (err.retry_after or 1) <= 5:
                await asyncio.sleep(err.retry_after or 1)
                continue
            raise err
        raise StorageError(503, "armazenamento indisponível", 2)  # pragma: no cover

    @override
    async def upload(self, path: str, data: Body, content_type: str, length: int | None = None) -> Item:
        p = clean_path(path)
        n = _length(data, length)
        headers = self._headers({"Content-Type": content_type, "Content-Length": str(n)})
        conn, resp = await _call("PUT", self._url("content", path=p), headers, data, timeout=900.0)
        try:
            raw = await asyncio.to_thread(resp.read)
        finally:
            conn.close()
        if resp.status >= 300:
            raise _error(resp.status, raw, _retry_after(resp))
        return Item.of(json.loads(raw))

    @override
    async def download(self, path: str) -> Download:
        p = clean_path(path)
        conn, resp = await _call("GET", self._url("content", path=p), self._headers())
        if resp.status >= 300:
            try:
                raw = await asyncio.to_thread(resp.read)
            finally:
                conn.close()
            raise _error(resp.status, raw, _retry_after(resp))
        n = resp.getheader("content-length")
        return Download(
            resp.getheader("content-type") or "application/octet-stream",
            int(n) if n is not None and n.isdigit() else None,
            resp,
            conn,
        )

    @override
    async def list(self, prefix: str = "") -> builtins.list[Item]:
        v = await self._json("GET", self._url("list", prefix=clean_path(prefix, file=False)))
        return [Item.of(x) for x in v.get("items", [])]

    @override
    async def meta(self, path: str) -> Item:
        return Item.of(await self._json("GET", self._url("meta", path=clean_path(path))))

    @override
    async def delete(self, path: str) -> None:
        await self._json("DELETE", self._url("content", path=clean_path(path)))

    @override
    async def move(self, src: str, dst: str) -> Item:
        body = json.dumps({"from": clean_path(src), "to": clean_path(dst)}).encode()
        return Item.of(await self._json("POST", self._url("move"), body, retry=False))

    @override
    async def share_link(self, path: str, kind: str = "view", expires_hours: int = 24) -> ShareLink:
        body = json.dumps({"path": clean_path(path), "kind": kind, "expires_hours": expires_hours}).encode()
        v = await self._json("POST", self._url("share-link"), body, retry=False)
        return ShareLink(url=v["url"], kind=v.get("kind", kind), expires=v.get("expires"))


def bound() -> list[tuple[str, str]]:
    """Connections linked to this app × environment (``HUB_CONNECTIONS`` = ``name=type,…``)."""
    out: list[tuple[str, str]] = []
    for part in os.environ.get("HUB_CONNECTIONS", "").split(","):
        name, _, kind = part.strip().partition("=")
        if name and kind:
            out.append((name, kind))
    return out


class LocalConnection(Connection):
    """Local folder with the same API (``hub dev``: ``HUB_STORAGE_LOCAL_DIR``). No share links."""

    def __init__(self, name: str, root: str) -> None:
        super().__init__(name, "local")
        self.root = os.path.abspath(os.path.join(root, name))

    @override
    def _copy(self) -> Connection:
        return LocalConnection(self.name, os.path.dirname(self.root))

    def _p(self, path: str, file: bool = True) -> str:
        rel = clean_path(path, file)
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def _item(self, full: str) -> Item:
        st = os.stat(full)
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        when = dt.datetime.fromtimestamp(st.st_mtime, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        folder = os.path.isdir(full)
        return Item(rel, os.path.basename(full), 0 if folder else st.st_size, when, folder)

    def _upload(self, path: str, raw: bytes) -> Item:
        full = self._p(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(raw)
        return self._item(full)

    def _open(self, path: str) -> Download:
        full = self._p(path)
        if not os.path.isfile(full):
            raise StorageError(404, "Arquivo não encontrado.")
        f = open(full, "rb")  # noqa: SIM115 — closed by Download.aclose
        return Download("application/octet-stream", os.path.getsize(full), f, f)

    def _list(self, prefix: str) -> builtins.list[Item]:
        d = self._p(prefix, file=False)
        if not os.path.isdir(d):
            return []
        return [self._item(os.path.join(d, n)) for n in sorted(os.listdir(d))]

    def _meta(self, path: str) -> Item:
        full = self._p(path)
        if not os.path.exists(full):
            raise StorageError(404, "Arquivo não encontrado.")
        return self._item(full)

    def _delete(self, path: str) -> None:
        full = self._p(path)
        if os.path.isfile(full):
            os.remove(full)

    def _move(self, src: str, dst: str) -> Item:
        a, b = self._p(src), self._p(dst)
        if not os.path.exists(a):
            raise StorageError(404, "Arquivo não encontrado.")
        os.makedirs(os.path.dirname(b), exist_ok=True)
        os.replace(a, b)
        return self._item(b)

    @override
    async def upload(self, path: str, data: Body, content_type: str, length: int | None = None) -> Item:
        raw = bytes(data) if isinstance(data, bytes | bytearray) else data.read()
        return await asyncio.to_thread(self._upload, path, raw)

    @override
    async def download(self, path: str) -> Download:
        return await asyncio.to_thread(self._open, path)

    @override
    async def list(self, prefix: str = "") -> builtins.list[Item]:
        return await asyncio.to_thread(self._list, prefix)

    @override
    async def meta(self, path: str) -> Item:
        return await asyncio.to_thread(self._meta, path)

    @override
    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete, path)

    @override
    async def move(self, src: str, dst: str) -> Item:
        return await asyncio.to_thread(self._move, src, dst)


def connection(name: str) -> Connection | None:
    """Connection ``name`` through the hub's broker, if linked to this app × environment
    (in ``hub dev``, ``HUB_STORAGE_LOCAL_DIR`` = local folder with the same API)."""
    local = os.environ.get("HUB_STORAGE_LOCAL_DIR", "").strip()
    if local:
        return LocalConnection(name, local)
    kind = next((k for n, k in bound() if n == name), None)
    base = os.environ.get("HUB_STORAGE_URL", "")
    token = os.environ.get("HUB_STORAGE_TOKEN", "")
    if kind is None or not base or len(token) < 16:
        return None
    return BrokerConnection(name, kind, base, token)


def s3_direct(name: str, cfg: S3Config) -> Connection:
    """App's own bucket (direct S3/MinIO)."""
    return S3Connection(name, cfg)
