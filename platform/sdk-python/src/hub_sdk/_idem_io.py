"""Request/response plumbing of :mod:`hub_sdk.idempotency`: the stored response, the notices the
middleware answers with, the bounded response capture and reading/re-delivering the request body."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from hub_sdk.idempotency import RequestHasher

log = logging.getLogger("hub_sdk.idempotency")

REPLAY_HEADER = "idempotent-replay"
MAX_HASHED_BODY = 1024 * 1024
MAX_STORED_BODY = 256 * 1024
KEPT_HEADERS = ("content-type", "location", "retry-after", "cache-control")
CHUNK = 64 * 1024

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass
class _Stored:
    status: int
    headers: list[list[str]]
    body: bytes | None


@dataclass
class _MemEntry:
    hash: str
    resp: _Stored | None
    expires: float


def _notice(status: int, title: str, text: str, extra: list[tuple[bytes, bytes]] | None = None) -> _Stored:
    kind = "ok" if 200 <= status < 300 else "error"
    body = (
        f'<div class="hub-notice hub-notice-{kind}" role="alert"><strong>{title}</strong><p>{text}</p></div>'
    ).encode()
    h = [["content-type", "text/html; charset=utf-8"], ["cache-control", "no-store"]]
    for k, v in extra or []:
        h.append([k.decode(), v.decode()])
    return _Stored(status, h, body)


def _already_done() -> tuple[_Stored, bool]:
    return (
        _notice(
            200, "Operação já concluída", "Esta solicitação já tinha sido processada; nada foi repetido."
        ),
        True,
    )


def _mismatch() -> _Stored:
    return _notice(
        422,
        "Chave de idempotência reutilizada",
        "A mesma chave foi usada para uma solicitação diferente. Recarregue a página e tente de novo.",
    )


def _in_progress() -> _Stored:
    return _notice(
        409,
        "Solicitação em andamento",
        "Esta mesma solicitação ainda está sendo processada. Aguarde alguns segundos.",
        [(b"retry-after", b"2")],
    )


def _unavailable(e: object) -> _Stored:
    log.error("hub_sdk: idempotência indisponível: %s", e)
    return _notice(
        503,
        "Serviço temporariamente indisponível",
        "Não foi possível registrar a solicitação. Tente novamente em instantes.",
    )


async def _send_stored(send: Send, r: _Stored, replay: bool) -> None:
    if r.body is None:
        r, replay = _already_done()
    headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in r.headers]
    if replay:
        headers.append((REPLAY_HEADER.encode(), b"true"))
    body = r.body or b""
    headers = [h for h in headers if h[0].lower() != b"content-length"]
    headers.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": r.status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class _Captured:
    """The handler's ``send`` with a **limited buffer**: up to ``MAX_STORED_BODY`` the response stays
    in memory and only goes to the client after being recorded (:meth:`flush`); a
    replay that arrives right after already finds ``done``. Once over the cap, what was in the buffer is
    sent and the rest goes **straight to the client** (streaming; nothing beyond the cap stays
    in memory) and only the "completed" mark is stored. If the client is gone (``send``
    failed), the handler runs to the end without error — the action may already have been committed and the
    result still needs to be recorded."""

    def __init__(self, send: Send) -> None:
        self._send = send
        self.status = 500
        self.headers: list[tuple[bytes, bytes]] = []
        self._pending: list[Message] = []
        self.size = 0
        self.overflow = False
        self.started = False
        self.client_gone = False

    async def _out(self, msg: Message) -> None:
        if self.client_gone:
            return
        try:
            await self._send(msg)
        except OSError:
            self.client_gone = True
        except Exception as e:  # client disconnect (Starlette/anyio names)
            if e.__class__.__name__ in ("ClientDisconnect", "ClosedResourceError", "BrokenResourceError"):
                self.client_gone = True
            else:
                raise

    async def __call__(self, msg: Message) -> None:
        if msg["type"] == "http.response.start":
            self.started = True
            self.status = int(msg["status"])
            self.headers = list(msg.get("headers", []))
        elif msg["type"] == "http.response.body" and not self.overflow:
            self.size += len(msg.get("body", b""))
            if self.size > MAX_STORED_BODY:
                self.overflow = True
                pending, self._pending = self._pending, []
                for m in pending:
                    await self._out(m)
        if self.overflow:
            await self._out(msg)
        else:
            self._pending.append(msg)

    async def flush(self) -> None:
        """Sends what is in the buffer (call after recording the result)."""
        pending, self._pending = self._pending, []
        for m in pending:
            await self._out(m)

    def stored_body(self) -> bytes | None:
        """Body for replay (``None`` = above the cap → "completed")."""
        if self.overflow:
            return None
        return b"".join(bytes(m.get("body", b"")) for m in self._pending if m["type"] == "http.response.body")

    def kept(self) -> list[list[str]]:
        out = []
        for k, v in self.headers:
            name = k.decode("latin-1").lower()
            if name in KEPT_HEADERS:
                out.append([name, v.decode("latin-1")])
        return out


class _BodyError(Exception):
    pass


class _TooLarge(Exception):
    pass


async def _next_chunk(receive: Receive) -> tuple[bytes, bool]:
    """``(body, more_body)`` of the next request message; a disconnect is a :class:`_BodyError`."""
    msg = await receive()
    if msg["type"] == "http.disconnect":
        raise _BodyError
    return cast("bytes", msg.get("body", b"")), bool(msg.get("more_body", False))


async def _read_in_memory(receive: Receive, hasher: RequestHasher) -> bytes:
    parts: list[bytes] = []
    total = 0
    more = True
    while more:
        b, more = await _next_chunk(receive)
        total += len(b)
        # The declared size was small: a client sending more than it announced is cut off.
        if total > MAX_HASHED_BODY + 1:
            raise _BodyError
        if b:
            parts.append(bytes(b))
    body = b"".join(parts)
    hasher.update(body)
    return body


async def _copy_body(receive: Receive, hasher: RequestHasher, f: IO[bytes], max_body: int) -> None:
    total = 0
    more = True
    while more:
        b, more = await _next_chunk(receive)
        if not b:
            continue
        total += len(b)
        if total > max_body:
            raise _TooLarge
        hasher.update(bytes(b))
        f.write(b)


async def _spool(receive: Receive, hasher: RequestHasher, max_body: int) -> str:
    fd, path = tempfile.mkstemp(prefix="hub-idem-", dir=tempfile.gettempdir())
    try:
        with os.fdopen(fd, "wb") as f:
            await _copy_body(receive, hasher, f, max_body)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return path


async def _read_body(
    receive: Receive, hasher: RequestHasher, declared: int | None, max_body: int
) -> tuple[bytes | None, str | None]:
    """Small body with a declared size → memory; otherwise a temporary file."""
    if declared is not None and declared <= MAX_HASHED_BODY:
        return await _read_in_memory(receive, hasher), None
    return None, await _spool(receive, hasher, max_body)


def _replayer(receive: Receive, body: bytes | None, path: str | None) -> Receive:
    state: dict[str, Any] = {"done": False, "f": None, "sent_mem": False}

    async def recv() -> Message:
        if state["done"]:
            return await receive()
        if body is not None:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        if state["f"] is None:
            state["f"] = open(path or "", "rb")  # noqa: SIM115, ASYNC230 — closed at the end; short local read
        chunk = state["f"].read(CHUNK)
        if not chunk:
            state["f"].close()
            state["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    return recv


def _row_stored(r: Any) -> _Stored:
    headers = r["headers"] if isinstance(r["headers"], list) else []
    return _Stored(
        status=r["status"] or 200,
        headers=[[str(h[0]), str(h[1])] for h in headers if isinstance(h, list) and len(h) == 2],
        body=bytes(r["body"]) if r["body"] is not None else None,
    )
