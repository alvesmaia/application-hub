"""Armazenamento do broker (`hub-storage`): caminhos confinados, erros, itens e etapas de teste."""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

OPS = ("upload", "download", "list", "delete", "move", "share_link")
_MAX_TOTAL, _MAX_SEGMENT, _MAX_DEPTH = 1024, 255, 32
_LOOKALIKES = set(
    "\u2024\uff0e\ufe52\u2215\u2044\uff0f\u29f8\uff3c\u29f9\ufe68\u200b\u200e\u200f\u202e\ufeff"
)
_FORBIDDEN = set(r'\%:*?"<>|#')
_STATUS = {
    "invalid": 400,
    "not_found": 404,
    "conflict": 409,
    "too_large": 413,
    "unavailable": 503,
    "auth": 502,
    "forbidden": 502,
    "upstream": 502,
}


class StorageError(Exception):
    """`kind` define o status devolvido ao app; `detail` (texto do provedor) fica só na auditoria."""

    def __init__(self, kind: str, message: str, retry_after: int | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.kind, self.message, self.retry_after, self.detail = kind, message, retry_after, detail

    @property
    def status(self) -> int:
        return _STATUS[self.kind]

    def admin_text(self) -> str:
        return f"{self.message} — {self.detail}" if self.detail else self.message


@dataclass
class Item:
    path: str
    name: str
    size: int = 0
    modified: str = ""
    folder: bool = False
    content_type: str | None = None
    etag: str | None = None

    def json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _bad(message: str) -> StorageError:
    return StorageError("invalid", f"Caminho inválido: {message}.")


def clean(p: str) -> str:
    """Normaliza um caminho relativo e recusa tudo que poderia escapar da pasta-base."""
    if len(p.encode()) > _MAX_TOTAL:
        raise StorageError("invalid", "Caminho longo demais.")
    segs = [s for s in p.split("/") if s]
    for seg in segs:
        if seg in (".", ".."):
            raise _bad('".." e "." não são permitidos')
        if len(seg.encode()) > _MAX_SEGMENT:
            raise _bad("nome longo demais")
        if seg.endswith((" ", ".")) or seg.startswith(" "):
            raise _bad("nomes não podem começar com espaço nem terminar em espaço ou ponto")
        for c in seg:
            if ord(c) < 32 or 0x7F <= ord(c) < 0xA0:
                raise _bad("caractere de controle")
            if c in _FORBIDDEN:
                raise _bad(r'caractere não permitido (\ % : * ? " < > | #)')
            if c in _LOOKALIKES:
                raise _bad("caractere Unicode parecido com ponto ou barra")
    if len(segs) > _MAX_DEPTH:
        raise StorageError("invalid", "Pastas aninhadas demais.")
    return "/".join(segs)


def clean_file(p: str) -> str:
    c = clean(p)
    if not c:
        raise StorageError("invalid", "Informe o caminho do arquivo.")
    return c


def join(base: str, rel: str) -> str:
    return f"{base}/{rel}" if base and rel else base or rel


def encode(p: str) -> str:
    return quote(p, safe="-_.~/")


def split_parent(p: str) -> tuple[str, str]:
    a, _, b = p.rpartition("/")
    return a, b


def rfc3339(unix_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(unix_s))


def parse_rfc3339(s: str) -> float | None:
    m = re.fullmatch(r"(\d{4}-\d\d-\d\d)[Tt ](\d\d:\d\d:\d\d)(?:\.\d+)?(Z|z|[+-]\d\d:\d\d)", s.strip())
    if not m:
        return None
    try:
        base = calendar.timegm(time.strptime(f"{m[1]}T{m[2]}", "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    off = 0 if m[3] in "Zz" else (1 if m[3][0] == "+" else -1) * (int(m[3][1:3]) * 3600 + int(m[3][4:6]) * 60)
    return base - off


def redact(msg: str, secrets: list[str]) -> str:
    for s in secrets:
        if len(s) >= 4:
            msg = msg.replace(s, "••••")
    return msg


class Steps:
    """Etapas do teste de conexão: a primeira que falha pula as demais."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.failed = False

    async def run(self, key: str, fn: Callable[[], Awaitable[tuple[Any, str]]]) -> Any | None:
        if self.failed:
            self.steps.append({"key": key, "ok": None, "detail": "", "ms": 0})
            return None
        t0 = time.monotonic()
        try:
            value, detail = await fn()
        except StorageError as e:
            self.failed = True
            self.steps.append({"key": key, "ok": False, "detail": e.admin_text(), "ms": _ms(t0)})
            return None
        self.steps.append({"key": key, "ok": True, "detail": detail, "ms": _ms(t0)})
        return True if value is None else value


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


class Body:
    """Corpo do envio: leitura exata em blocos e conferência do tamanho declarado."""

    def __init__(self, stream: AsyncIterator[bytes]) -> None:
        self.stream, self.carry = stream, b""

    async def read(self, n: int) -> bytes:
        buf = bytearray(self.carry[:n])
        self.carry = self.carry[n:]
        async for chunk in self.stream:
            if len(buf) + len(chunk) > n:
                take = n - len(buf)
                buf += chunk[:take]
                self.carry = chunk[take:]
                break
            buf += chunk
            if len(buf) == n:
                break
        if len(buf) < n:
            raise StorageError("invalid", "O corpo terminou antes do tamanho declarado (Content-Length).")
        return bytes(buf)

    async def expect_end(self) -> None:
        if self.carry or any([c async for c in self.stream if c]):
            raise StorageError("invalid", "O corpo é maior que o tamanho declarado (Content-Length).")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.carry:
            yield self.carry
            self.carry = b""
        async for chunk in self.stream:
            yield chunk
