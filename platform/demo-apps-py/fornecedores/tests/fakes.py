"""Armazenamento em memória no lugar do S3/broker (mesma interface de ``hub_sdk.storage.Connection``)."""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from typing import Any

from hub_sdk.storage import Connection, Item, StorageError
from typing_extensions import override


class FakeDownload:
    def __init__(self, content_type: str, data: bytes) -> None:
        self.content_type = content_type
        self.length: int | None = len(data)
        self.data = data

    async def chunks(self) -> AsyncIterator[bytes]:
        yield self.data


class FakeStore(Connection):
    """Objetos em memória; ``fail`` (status, mensagem) faz toda operação falhar."""

    def __init__(self, kind: str = "s3") -> None:
        super().__init__("fake", kind)
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail: tuple[int, str] | None = None
        self.broker = False

    @property
    @override
    def via_broker(self) -> bool:
        return self.broker

    @override
    def _copy(self) -> Connection:
        return self

    def _check(self) -> None:
        if self.fail:
            raise StorageError(self.fail[0], self.fail[1])

    @override
    async def upload(self, path: str, data: Any, content_type: str, length: int | None = None) -> Item:
        self._check()
        body = data if isinstance(data, bytes) else data.read()
        self.objects[path] = (body, content_type)
        return Item(path=path, name=path.rsplit("/", 1)[-1], size=len(body), content_type=content_type)

    @override
    async def download(self, path: str) -> Any:
        self._check()
        if path not in self.objects:
            raise StorageError(404, "não encontrado")
        body, ct = self.objects[path]
        return FakeDownload(ct, body)

    @override
    async def list(self, prefix: str = "") -> builtins.list[Item]:
        self._check()
        return [
            Item(
                path=k,
                name=k.rsplit("/", 1)[-1],
                size=len(v[0]),
                modified="2026-10-01T12:34:56Z",
                content_type=v[1],
            )
            for k, v in sorted(self.objects.items())
            if k.startswith(prefix + "/")
        ]

    @override
    async def delete(self, path: str) -> None:
        self._check()
        if self.objects.pop(path, None) is None:
            raise StorageError(404, "não encontrado")
