"""``Idempotency-Key`` para rotas que alteram dados (POST/PUT/PATCH/DELETE) — middleware
ASGI com a mesma semântica e a **mesma tabela** do SDK Rust (``hub_idempotency``).

- Primeira vez: o pedido segue para o handler; a resposta (2xx/3xx/4xx, exceto 408/429)
  é guardada. 5xx/429 não são guardados — salvo se o handler já efetivou a ação
  (:meth:`IdemCtx.mark_committed`).
- Repetição com o mesmo pedido: devolve a resposta guardada (``idempotent-replay: true``).
- Mesma chave com **outro** pedido (hash diferente): **422**.
- Mesma chave em paralelo: espera até ``wait`` (5 s) pela primeira e devolve a resposta
  dela; se não terminar, **409** com ``Retry-After: 2``.
- Escopo da chave: valor do ``x-hub-user`` (injetado pelo hub) + método + caminho.
- Hash: método, caminho, query, ``Content-Type`` sem ``boundary`` e o **corpo inteiro**
  (``boundary`` do multipart normalizado, inclusive partido entre pedaços). Até 1 MiB
  com ``Content-Length`` em memória; acima disso ou sem tamanho (chunked), em streaming
  para um temporário em ``$TMPDIR`` (``/scratch``), reentregue ao handler e apagado no
  fim — inclusive em exceção. O hash é idêntico ao do Rust (mesmos vetores de teste).
- **Teto do corpo** (``HUB_MAX_BODY_MB``, padrão 64): ``Content-Length`` acima do teto →
  **413** sem ler nada; corpo sem tamanho (chunked) que passa do teto → 413 e o temporário
  é apagado na hora. Nada acima do teto chega ao disco.
- **Resposta com buffer limitado**: até ``MAX_STORED_BODY`` a resposta é registrada antes
  de ir ao cliente; acima disso segue direto ao cliente em streaming (nada é
  acumulado em memória além do teto de replay, 256 KiB) e guarda-se só a marca
  "concluída" (como no Rust):
  a repetição recebe 200 "Operação já concluída".

**Queda no meio** (Postgres): o dono da chave segura um advisory lock de sessão
(``pg_try_advisory_lock(hashtextextended(rota||chave, 7))`` — o mesmo do Rust) numa
conexão do pool de idempotência enquanto o handler executa. Se o processo morre, o lock
morre com a conexão; a nova tentativa encontra ``pending`` sem dono e executa de novo —
seguro porque a transação de negócio não confirmou (senão o registro estaria
``committed``). Se confirmou mas a resposta não foi guardada, a repetição recebe 200
"Operação já concluída".
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import random
import tempfile
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from starlette.requests import HTTPConnection

log = logging.getLogger("hub_sdk.idempotency")

HEADER = "idempotency-key"
USER_HEADER = "x-hub-user"
REPLAY_HEADER = "idempotent-replay"
MAX_HASHED_BODY = 1024 * 1024
MAX_STORED_BODY = 256 * 1024
KEPT_HEADERS = ("content-type", "location", "retry-after", "cache-control")
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
CHUNK = 64 * 1024
DEFAULT_MAX_BODY_MB = 64


def max_body_from_env() -> int:
    """Teto do corpo de pedidos com ``Idempotency-Key`` (``HUB_MAX_BODY_MB``, padrão 64)."""
    try:
        mb = int(os.environ.get("HUB_MAX_BODY_MB", DEFAULT_MAX_BODY_MB))
    except ValueError:
        mb = DEFAULT_MAX_BODY_MB
    return max(1, mb) * 1024 * 1024


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def valid_key(k: str) -> bool:
    """8 a 128 caracteres visíveis ASCII."""
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
    """Hash incremental do pedido (idêntico ao ``RequestHasher`` do Rust)."""

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
    """2xx/3xx/4xx (determinísticas), exceto 408/429; nunca 5xx."""
    return not (status >= 500 or status in (408, 429))


@dataclass
class IdemCtx:
    """Chave em curso (``request.state.hub_idem``): o handler grava a marca de
    efetivação **na própria transação** de negócio."""

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
    """Contexto da chave da requisição (``None`` sem ``Idempotency-Key``)."""
    ctx = getattr(conn.state, "hub_idem", None)
    return ctx if isinstance(ctx, IdemCtx) else None


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


class Idempotency:
    """Backend: Postgres (``pool`` dedicado) ou memória (``pool=None``)."""

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
    """``send`` do handler com **buffer limitado**: até ``MAX_STORED_BODY`` a resposta fica
    em memória e só vai ao cliente depois de registrada (:meth:`flush`) — igual ao Rust, a
    repetição que chega logo depois já encontra ``done``. Passou do teto, o que estava no
    buffer é enviado e o resto segue **direto ao cliente** (streaming; nada além do teto
    fica em memória) e só a marca "concluída" é guardada. Se o cliente sumiu (``send``
    falhou), o handler segue até o fim sem erro — a ação pode já ter sido efetivada e o
    resultado ainda precisa ser registrado."""

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
        except Exception as e:  # desconexão do cliente (nomes do Starlette/anyio)
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
        """Envia o que ficou no buffer (chamar depois de registrar o resultado)."""
        pending, self._pending = self._pending, []
        for m in pending:
            await self._out(m)

    def stored_body(self) -> bytes | None:
        """Corpo para replay (``None`` = acima do teto → "já concluída")."""
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
        user = headers.get(USER_HEADER, "").strip()
        path = scope.get("path", "")
        route = f"{user}\n{scope['method']} {path}"
        query = scope.get("query_string", b"").decode("latin-1")
        hasher = RequestHasher(scope["method"], path, query, headers.get("content-type", ""))
        declared: int | None
        try:
            declared = int(headers["content-length"]) if "content-length" in headers else None
        except ValueError:
            declared = None
        too_big = _notice(
            413,
            "Envio muito grande",
            f"O limite desta aplicação é {self.max_body >> 20} MB por envio.",
        )
        if declared is not None and declared > self.max_body:
            await _send_stored(send, too_big, False)
            return
        spool_path: str | None = None
        try:
            try:
                body_mem, spool_path = await _read_body(receive, hasher, declared, self.max_body)
            except _TooLarge:
                await _send_stored(send, too_big, False)
                return
            except _BodyError:
                await _send_stored(
                    send,
                    _notice(400, "Envio interrompido", "O corpo da requisição não pôde ser lido."),
                    False,
                )
                return
            digest = hasher.finish()
            replayed = _replayer(receive, body_mem, spool_path)
            idem = self.idem
            if idem.pool is not None:
                await _pg_flow(idem, self.app, scope, replayed, send, route, key, digest)
            else:
                await _mem_flow(idem, self.app, scope, replayed, send, route, key, digest)
        finally:
            if spool_path is not None and spool_path:
                with contextlib.suppress(OSError):
                    os.remove(spool_path)


async def _read_body(
    receive: Receive, hasher: RequestHasher, declared: int | None, max_body: int
) -> tuple[bytes | None, str | None]:
    """Corpo pequeno com tamanho declarado → memória; senão, arquivo temporário."""
    if declared is not None and declared <= MAX_HASHED_BODY:
        parts: list[bytes] = []
        total = 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                raise _BodyError
            b = msg.get("body", b"")
            if b:
                total += len(b)
                if total > MAX_HASHED_BODY + 1:
                    raise _BodyError
                parts.append(bytes(b))
            if not msg.get("more_body", False):
                break
        body = b"".join(parts)
        hasher.update(body)
        return body, None
    fd, path = tempfile.mkstemp(prefix="hub-idem-", dir=tempfile.gettempdir())
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                msg = await receive()
                if msg["type"] == "http.disconnect":
                    raise _BodyError
                b = msg.get("body", b"")
                if b:
                    total += len(b)
                    if total > max_body:
                        raise _TooLarge
                    hasher.update(bytes(b))
                    f.write(b)
                if not msg.get("more_body", False):
                    break
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return None, path


def _replayer(receive: Receive, body: bytes | None, path: str | None) -> Receive:
    state: dict[str, Any] = {"done": False, "f": None, "sent_mem": False}

    async def recv() -> Message:
        if state["done"]:
            return await receive()
        if body is not None:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        if state["f"] is None:
            state["f"] = open(path or "", "rb")  # noqa: SIM115, ASYNC230 — fechado ao terminar; leitura local curta
        chunk = state["f"].read(CHUNK)
        if not chunk:
            state["f"].close()
            state["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    return recv


async def _run_app(app: ASGIApp, scope: Scope, receive: Receive, send: Send, ctx: IdemCtx) -> _Captured:
    """Executa o handler repassando a resposta ao cliente em streaming (``_Captured``)."""
    scope.setdefault("state", {})["hub_idem"] = ctx
    cap = _Captured(send)
    await app(scope, receive, cap)
    return cap


def _row_stored(r: Any) -> _Stored:
    headers = r["headers"] if isinstance(r["headers"], list) else []
    return _Stored(
        status=r["status"] or 200,
        headers=[[str(h[0]), str(h[1])] for h in headers if isinstance(h, list) and len(h) == 2],
        body=bytes(r["body"]) if r["body"] is not None else None,
    )


SELECT = (
    "SELECT request_hash, state, status, headers, body FROM hub_idempotency "
    "WHERE route = $1 AND key = $2 AND expires_at > now()"
)


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
    healthy = True
    locked = False
    try:
        deadline = time.monotonic() + idem.wait
        while True:
            try:
                got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 7))", lock_key)
            except Exception as e:
                healthy = False
                await _send_stored(send, _unavailable(e), False)
                return
            if got:
                locked = True
                break
            with contextlib.suppress(Exception):
                r = await conn.fetchrow(SELECT, route, key)
                if r is not None:
                    if r["request_hash"] != digest:
                        await _send_stored(send, _mismatch(), False)
                        return
                    if r["state"] == "done":
                        await _send_stored(send, _row_stored(r), True)
                        return
            if time.monotonic() >= deadline:
                await _send_stored(send, _in_progress(), False)
                return
            await asyncio.sleep(0.1)
        try:
            existing = await conn.fetchrow(SELECT, route, key)
        except Exception as e:
            await _send_stored(send, _unavailable(e), False)
            return
        if existing is not None:
            if existing["request_hash"] != digest:
                await _send_stored(send, _mismatch(), False)
                return
            if existing["state"] == "done":
                await _send_stored(send, _row_stored(existing), True)
                return
            if existing["state"] == "committed":
                done, rep = _already_done()
                await _send_stored(send, done, rep)
                return
            # pending sem dono: o anterior morreu antes de efetivar
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
        committed = False
        with contextlib.suppress(Exception):
            committed = (
                await conn.fetchval(
                    "SELECT state FROM hub_idempotency WHERE route = $1 AND key = $2", route, key
                )
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
        await cap.flush()
    finally:
        ok = healthy
        if locked and ok:  # ty: ignore[redundant-condition-strict]  # `healthy` muda no except que retorna; o ty nao enxerga no finally
            try:
                await asyncio.shield(
                    conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 7))", lock_key)
                )
            except BaseException:
                ok = False
        if not ok:
            conn.terminate()
        with contextlib.suppress(Exception):
            await asyncio.shield(pool.release(conn))


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
        e = idem._mem.get(ident)
        if e is not None and e.hash != digest:
            await _send_stored(send, _mismatch(), False)
        elif e is not None and e.resp is not None:
            await _send_stored(send, e.resp, True)
        else:
            await _send_stored(send, _in_progress(), False)
        return
    try:
        now = time.monotonic()
        for k in [k for k, v in idem._mem.items() if v.expires <= now]:
            del idem._mem[k]
        e = idem._mem.get(ident)
        if e is not None:
            if e.hash != digest:
                await _send_stored(send, _mismatch(), False)
                return
            if e.resp is not None:
                await _send_stored(send, e.resp, True)
                return
        idem._mem[ident] = _MemEntry(digest, None, now + idem.ttl)
        cap = await _run_app(app, scope, receive, send, IdemCtx(route, key, False))
        if storable(cap.status):
            idem._mem[ident].resp = _Stored(cap.status, cap.kept(), cap.stored_body())
        else:
            idem._mem.pop(ident, None)
        await cap.flush()
    finally:
        lock.release()
        if not lock.locked():
            idem._locks.pop(ident, None)
