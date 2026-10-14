"""``Idempotency-Key`` for routes that change data (POST/PUT/PATCH/DELETE) — ASGI middleware
that uses the ``hub_idempotency`` table (stable format: do not change without a migration).

- First time: the request goes on to the handler; the response (2xx/3xx/4xx, except 408/429)
  is stored. 5xx/429 are not stored — unless the handler already committed the action
  (:meth:`IdemCtx.mark_committed`).
- Replay with the same request: returns the stored response (``idempotent-replay: true``).
- Same key with **another** request (different hash): **422**.
- Same key in parallel: waits up to ``wait`` (5 s) for the first and returns its response;
  if it does not finish, **409** with ``Retry-After: 2``.
- Key scope: value of ``x-hub-user`` (injected by the hub) + method + path.
- Hash: method, path, query, ``Content-Type`` without ``boundary`` and the **whole body**
  (``boundary`` of the multipart normalized, including when split across chunks). Up to 1 MiB
  with ``Content-Length`` in memory; above that, or without a size (chunked), streamed
  to a temporary file in ``$TMPDIR`` (``/scratch``), re-delivered to the handler and deleted at the
  end — including on exception. The hash follows the fixed test vectors.
- **Body cap** (``HUB_MAX_BODY_MB``, default 64): ``Content-Length`` above the cap →
  **413** without reading anything; a body without size (chunked) that goes over the cap → 413 and the temp file
  is deleted right away. Nothing above the cap reaches the disk.
- **Response with limited buffer**: up to ``MAX_STORED_BODY`` the response is recorded before
  going to the client; above that it goes straight to the client in streaming (nothing beyond the replay cap,
  256 KiB, is accumulated in memory) and only the
  "completed" mark is stored:
  a replay gets 200 "Operação já concluída".

**Crash midway** (Postgres): the key's owner holds a session advisory lock on the
(``pg_try_advisory_lock(hashtextextended(rota||chave, 7))``) numa
idempotency pool connection while the handler runs. If the process dies, the lock
dies with the connection; the new attempt finds ``pending`` with no owner and runs again —
safe because the business transaction did not commit (otherwise the record would be
``committed``). If it committed but the response was not stored, the replay gets 200
"Operação já concluída".
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import HTTPConnection

from hub_sdk._idem_io import CHUNK as CHUNK
from hub_sdk._idem_io import KEPT_HEADERS as KEPT_HEADERS
from hub_sdk._idem_io import MAX_HASHED_BODY as MAX_HASHED_BODY
from hub_sdk._idem_io import MAX_STORED_BODY as MAX_STORED_BODY
from hub_sdk._idem_io import REPLAY_HEADER as REPLAY_HEADER
from hub_sdk._idem_io import ASGIApp as ASGIApp
from hub_sdk._idem_io import Message as Message
from hub_sdk._idem_io import Receive as Receive
from hub_sdk._idem_io import Scope as Scope
from hub_sdk._idem_io import Send as Send
from hub_sdk._idem_io import (
    _BodyError,
    _Captured,
    _in_progress,
    _MemEntry,
    _mismatch,
    _notice,
    _read_body,
    _replayer,
    _send_stored,
    _Stored,
    _TooLarge,
    _unavailable,
)
from hub_sdk._idem_pg import SELECT as SELECT
from hub_sdk._idem_pg import _pg_answer_existing, _pg_lock, _pg_release

log = logging.getLogger("hub_sdk.idempotency")

HEADER = "idempotency-key"
USER_HEADER = "x-hub-user"
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
DEFAULT_MAX_BODY_MB = 64


def max_body_from_env() -> int:
    """Body cap for requests with ``Idempotency-Key`` (``HUB_MAX_BODY_MB``, default 64)."""
    try:
        mb = int(os.environ.get("HUB_MAX_BODY_MB", DEFAULT_MAX_BODY_MB))
    except ValueError:
        mb = DEFAULT_MAX_BODY_MB
    return max(1, mb) * 1024 * 1024


def valid_key(k: str) -> bool:
    """8 to 128 visible ASCII characters."""
    return 8 <= len(k) <= 128 and all(33 <= ord(c) <= 126 for c in k)


def split_content_type(ct: str) -> tuple[str, str | None]:
    parts = ct.split(";")
    base = parts[0].strip().lower() if parts else ""
    boundary = None
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            if k.strip().lower() == "boundary":
                boundary = v.strip().strip('"')
                break
    return base, boundary


class RequestHasher:
    """Incremental hash of the request."""

    def __init__(self, method: str, path: str, query: str, content_type: str) -> None:
        ct, boundary = split_content_type(content_type)
        self._h = hashlib.sha256()
        for part in (method, path, query, ct):
            self._h.update(part.encode())
            self._h.update(b"\0")
        self._needle = boundary.encode() if boundary is not None and boundary else None
        self._carry = b""

    def update(self, data: bytes) -> None:
        needle = self._needle
        if needle is None:
            self._h.update(data)
            return
        buf = self._carry + data
        n = len(needle)
        run = pos = 0
        while True:
            j = buf.find(needle, pos)
            if j < 0:
                break
            self._h.update(buf[run:j])
            self._h.update(b"<boundary>")
            pos = run = j + n
        i = max(pos, len(buf) - n + 1)
        self._h.update(buf[run:i])
        self._carry = buf[i:]

    def finish(self) -> str:
        self._h.update(self._carry)
        self._carry = b""
        return self._h.hexdigest()


def request_hash(method: str, path: str, query: str, content_type: str, body: bytes) -> str:
    h = RequestHasher(method, path, query, content_type)
    h.update(body)
    return h.finish()


def storable(status: int) -> bool:
    """2xx/3xx/4xx (deterministic), except 408/429; never 5xx."""
    return not (status >= 500 or status in (408, 429))


@dataclass
class IdemCtx:
    """Key in progress (``request.state.hub_idem``): the handler writes the commit mark
    **in its own** business transaction."""

    route: str
    key: str
    durable: bool

    async def mark_committed(self, conn: Any) -> None:
        if not self.durable:
            return
        await conn.execute(
            "UPDATE hub_idempotency SET state = 'committed' WHERE route = $1 AND key = $2",
            self.route,
            self.key,
        )


def idem_ctx(conn: HTTPConnection) -> IdemCtx | None:
    """Context of the request's key (``None`` without ``Idempotency-Key``)."""
    ctx = getattr(conn.state, "hub_idem", None)
    return ctx if isinstance(ctx, IdemCtx) else None


class Idempotency:
    """Backend: Postgres (dedicated ``pool``) or memory (``pool=None``)."""

    def __init__(self, pool: Any | None, ttl: float = 24 * 3600, wait: float = 5.0) -> None:
        self.pool = pool
        self.ttl = ttl
        self.wait = wait
        self._mem: dict[tuple[str, str], _MemEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @classmethod
    def postgres(cls, pool: Any) -> Idempotency:
        return cls(pool)

    @classmethod
    def memory(cls) -> Idempotency:
        return cls(None)


class IdempotencyMiddleware:
    """``app.add_middleware(IdempotencyMiddleware, idem=Idempotency.postgres(pool))``."""

    def __init__(
        self,
        app: ASGIApp,
        idem: Idempotency | Callable[[], Idempotency],
        max_body: int | None = None,
    ) -> None:
        self.app = app
        self._idem = idem
        self.max_body = max_body if max_body is not None else max_body_from_env()

    @property
    def idem(self) -> Idempotency:
        return self._idem if isinstance(self._idem, Idempotency) else self._idem()

    def _too_big(self) -> _Stored:
        return _notice(
            413,
            "Envio muito grande",
            f"O limite desta aplicação é {self.max_body >> 20} MB por envio.",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        key = headers.get(HEADER)
        if key is None:
            await self.app(scope, receive, send)
            return
        key = key.strip()
        if not valid_key(key):
            await _send_stored(
                send,
                _notice(400, "Chave de idempotência inválida", "Use de 8 a 128 caracteres visíveis."),
                False,
            )
            return
        declared = _declared_length(headers)
        if declared is not None and declared > self.max_body:
            await _send_stored(send, self._too_big(), False)
            return
        user = headers.get(USER_HEADER, "").strip()
        path = scope.get("path", "")
        route = f"{user}\n{scope['method']} {path}"
        query = scope.get("query_string", b"").decode("latin-1")
        hasher = RequestHasher(scope["method"], path, query, headers.get("content-type", ""))
        await self._keyed(scope, receive, send, _Keyed(route, key, hasher, declared))

    async def _keyed(self, scope: Scope, receive: Receive, send: Send, req: _Keyed) -> None:
        """Reads (and hashes) the body, then runs the backend's flow; the spooled body never outlives it."""
        spool_path: str | None = None
        try:
            try:
                body_mem, spool_path = await _read_body(receive, req.hasher, req.declared, self.max_body)
            except _TooLarge:
                await _send_stored(send, self._too_big(), False)
                return
            except _BodyError:
                await _send_stored(
                    send,
                    _notice(400, "Envio interrompido", "O corpo da requisição não pôde ser lido."),
                    False,
                )
                return
            digest = req.hasher.finish()
            replayed = _replayer(receive, body_mem, spool_path)
            idem = self.idem
            flow = _pg_flow if idem.pool is not None else _mem_flow
            await flow(idem, self.app, scope, replayed, send, req.route, req.key, digest)
        finally:
            if spool_path is not None and spool_path:
                with contextlib.suppress(OSError):
                    os.remove(spool_path)


@dataclass
class _Keyed:
    """A mutating request that carries a valid key."""

    route: str
    key: str
    hasher: RequestHasher
    declared: int | None


def _declared_length(headers: dict[str, str]) -> int | None:
    try:
        return int(headers["content-length"]) if "content-length" in headers else None
    except ValueError:
        return None


async def _run_app(app: ASGIApp, scope: Scope, receive: Receive, send: Send, ctx: IdemCtx) -> _Captured:
    """Runs the handler, passing the response to the client in streaming (``_Captured``)."""
    scope.setdefault("state", {})["hub_idem"] = ctx
    cap = _Captured(send)
    await app(scope, receive, cap)
    return cap


async def _pg_flow(
    idem: Idempotency,
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    route: str,
    key: str,
    digest: str,
) -> None:
    lock_key = f"{route}\n{key}"
    try:
        pool: Any = idem.pool
        conn = await pool.acquire()
    except Exception as e:
        await _send_stored(send, _unavailable(e), False)
        return
    got: bool | None = False
    try:
        got = await _pg_lock(idem.wait, conn, send, lock_key, route, key, digest)
        if got is True:
            await _pg_owned(idem, conn, app, scope, receive, send, route, key, digest)
    finally:
        await _pg_release(pool, conn, lock_key, locked=got is True, healthy=got is not None)


async def _pg_owned(
    idem: Idempotency,
    conn: Any,
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    route: str,
    key: str,
    digest: str,
) -> None:
    """With the key's lock held: answer from the record, or run the handler and record its response."""
    try:
        existing = await conn.fetchrow(SELECT, route, key)
    except Exception as e:
        await _send_stored(send, _unavailable(e), False)
        return
    if existing is not None and await _pg_answer_existing(send, existing, digest):
        return
    try:
        await conn.execute(
            "INSERT INTO hub_idempotency (route, key, request_hash, state, expires_at) "
            "VALUES ($1, $2, $3, 'pending', now() + make_interval(secs => $4)) "
            "ON CONFLICT (route, key) DO UPDATE SET request_hash = excluded.request_hash, "
            "state = 'pending', status = NULL, headers = NULL, body = NULL, created_at = now(), "
            "expires_at = excluded.expires_at",
            route,
            key,
            digest,
            float(idem.ttl),
        )
    except Exception as e:
        await _send_stored(send, _unavailable(e), False)
        return
    if random.randrange(256) == 0:  # noqa: S311
        with contextlib.suppress(Exception):
            await conn.execute("DELETE FROM hub_idempotency WHERE expires_at < now()")
    cap = await _run_app(app, scope, receive, send, IdemCtx(route, key, True))
    await _pg_record(conn, route, key, cap)
    await cap.flush()


async def _pg_record(conn: Any, route: str, key: str, cap: _Captured) -> None:
    """Stores the response (or drops the pending mark), before it reaches the client."""
    committed = False
    with contextlib.suppress(Exception):
        committed = (
            await conn.fetchval("SELECT state FROM hub_idempotency WHERE route = $1 AND key = $2", route, key)
        ) == "committed"
    try:
        if storable(cap.status) or committed:
            await conn.execute(
                "UPDATE hub_idempotency SET state = 'done', status = $3, headers = $4, body = $5 "
                "WHERE route = $1 AND key = $2",
                route,
                key,
                cap.status,
                cap.kept(),
                cap.stored_body(),
            )
        else:
            await conn.execute(
                "DELETE FROM hub_idempotency WHERE route = $1 AND key = $2 AND state = 'pending'",
                route,
                key,
            )
    except Exception as e:
        log.error("hub_sdk: idempotência: resposta não guardada: %s", e)


def _mem_answer(e: _MemEntry | None, digest: str) -> tuple[_Stored, bool]:
    """Answer for a key that is busy or already has a record (422, replay or 409)."""
    if e is not None and e.hash != digest:
        return _mismatch(), False
    if e is not None and e.resp is not None:
        return e.resp, True
    return _in_progress(), False


async def _mem_flow(
    idem: Idempotency,
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    route: str,
    key: str,
    digest: str,
) -> None:
    ident = (route, key)
    lock = idem._locks.setdefault(ident, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), idem.wait)
    except TimeoutError:
        await _send_stored(send, *_mem_answer(idem._mem.get(ident), digest))
        return
    try:
        await _mem_owned(idem, app, scope, receive, send, ident, digest)
    finally:
        lock.release()
        if not lock.locked():
            idem._locks.pop(ident, None)


async def _mem_owned(
    idem: Idempotency,
    app: ASGIApp,
    scope: Scope,
    receive: Receive,
    send: Send,
    ident: tuple[str, str],
    digest: str,
) -> None:
    now = time.monotonic()
    for k in [k for k, v in idem._mem.items() if v.expires <= now]:
        del idem._mem[k]
    e = idem._mem.get(ident)
    if e is not None and (e.hash != digest or e.resp is not None):
        await _send_stored(send, *_mem_answer(e, digest))
        return
    idem._mem[ident] = _MemEntry(digest, None, now + idem.ttl)
    cap = await _run_app(app, scope, receive, send, IdemCtx(ident[0], ident[1], False))
    if storable(cap.status):
        idem._mem[ident].resp = _Stored(cap.status, cap.kept(), cap.stored_body())
    else:
        idem._mem.pop(ident, None)
    await cap.flush()
