"""Off-site copy of a backup package (`HUB_BACKUP_OFFSITE_URL`): an S3 bucket through `mc`, or a
`sharepoint://` connection; both read the copy back and check the sha256."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from hub.connections.provision import ObjectStore
from hub.ops.backup_db import _BUCKET, BackupDb, redact
from hub.ops.model import OpsError
from hub.storage import Body, StorageError


class BackupOffsite(BackupDb):
    """Base of `Backups`: needs `root` (the packages' folder), `objstore` and `conns`."""

    root: Path
    objstore: ObjectStore
    conns: Any

    async def offsite(self, app: str, env: str, name: str) -> str | None:
        """Copies the package to `HUB_BACKUP_OFFSITE_URL` (`http(s)://key:secret@host:port/bucket[/prefix]`) and reads it back
        checking the sha256. `None` = not configured; a failure raises `OpsError` (the local backup still counts)."""
        url = os.getenv("HUB_BACKUP_OFFSITE_URL", "").strip()
        if not url:
            return None
        if url.startswith("sharepoint://"):
            return await self._offsite_sharepoint(url, app, env, name)
        u = urlsplit(url)
        parts = [p for p in u.path.split("/") if p]
        if not (
            u.scheme in ("http", "https") and u.hostname and u.username and parts and _BUCKET.match(parts[0])
        ):
            raise OpsError("HUB_BACKUP_OFFSITE_URL inválida")
        sub = app if env != "staging" else f"{app}__staging"
        key, src = "/".join([*parts, sub, name]), self.root / sub / name
        port = f":{u.port}" if u.port else ""
        host = f"{u.scheme}://{quote(u.username)}:{quote(u.password or '')}@{u.hostname}{port}"
        with (
            tempfile.TemporaryDirectory(prefix="hub-off-") as cfg,
            tempfile.TemporaryDirectory(prefix="hub-offr-") as back,
        ):
            for args in (["cp", str(src), f"off/{key}"], ["cp", f"off/{key}", back + "/x.zip"]):
                rc, out = await self.objstore.runner(
                    ["--config-dir", cfg, "--quiet", *args], {"MC_HOST_off": host}
                )
                if rc != 0:
                    raise OpsError(f"cópia externa: {redact(out, u.password or '')}")
            if (
                hashlib.sha256((Path(back) / "x.zip").read_bytes()).digest()
                != hashlib.sha256(src.read_bytes()).digest()
            ):
                raise OpsError("cópia externa lida de volta diverge do pacote")
        return f"{u.hostname}/{key}"

    async def _offsite_sharepoint(self, url: str, app: str, env: str, name: str) -> str:
        """`sharepoint://<connection>[/folder]`: uploads the package through an existing SharePoint connection
        (Administration › Connections; credentials stay in the vault) and downloads it back checking the sha256."""
        conn, _, folder = url.removeprefix("sharepoint://").partition("/")
        c = self.conns.get(conn) if self.conns else None
        if not c or c.get("kind") != "sharepoint":
            raise OpsError(f"cópia externa: conexão SharePoint {conn!r} não encontrada")
        sub = app if env != "staging" else f"{app}__staging"
        rel, src = "/".join(p for p in (*folder.split("/"), sub, name) if p), self.root / sub / name
        data = src.read_bytes()

        async def feed() -> AsyncIterator[bytes]:
            for i in range(0, len(data), 4 << 20):
                yield data[i : i + (4 << 20)]

        client = self.conns.backend(c)
        try:
            await client.upload(rel, len(data), "application/zip", Body(feed()))
            _, _, body = await client.download(rel)
            back = b"".join([chunk async for chunk in body])
        except StorageError as e:
            raise OpsError(f"cópia externa (SharePoint): {e.admin_text()}") from e
        if hashlib.sha256(back).digest() != hashlib.sha256(data).digest():
            raise OpsError("cópia externa lida de volta diverge do pacote")
        return f"SharePoint {conn}/{rel}"
