"""Armazenamento de arquivos do app (onda 10, contrato item 22) — mesma interface do Rust
(``hub_sdk::storage``): conexões da plataforma pelo **broker do hub** (SharePoint, S3) ou o
bucket próprio do app (**S3 direto**, ``HUB_S3_*``). Trocar de backend é configuração
(vínculo em Administração › Conexões), não código.

.. code-block:: python

    docs = storage.connection("contratos-docs")      # None se não vinculada
    if docs:
        docs = docs.for_user(req.headers.get("x-hub-user"), req.headers.get("x-hub-user-sig"))
        await docs.upload("2026/CT-1.pdf", upload.file, "application/pdf", length=n)
        d = await docs.download("2026/CT-1.pdf")     # d.chunks(): async iterator

- **Broker** (``HUB_CONNECTIONS`` lista a conexão; ``HUB_STORAGE_URL`` + ``HUB_STORAGE_TOKEN``):
  o hub guarda a credencial e executa a operação confinada à pasta-base da conexão; o app
  nunca vê o segredo do cliente nem um token do Graph.
- **S3 direto** (:func:`s3_direct`): SigV4 contra o bucket do app.

Sem dependências novas: HTTP com ``http.client`` em ``asyncio.to_thread`` (upload a partir de
arquivo/bytes sem carregar tudo na memória; download em pedaços). Caminhos validados aqui com
as mesmas regras do hub (:func:`clean_path`).
"""

from __future__ import annotations

import asyncio
import builtins
import datetime as dt
import hashlib
import hmac
import http.client
import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import IO, Any, cast
from urllib.parse import quote, urlsplit

from typing_extensions import override

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

_LOOKALIKES = set("․﹒．∕⁄／⧸＼⧹﹨​‎‏‮﻿")
_BAD = set('\\%:*?"<>|#')
CHUNK = 64 * 1024


class StorageError(Exception):
    """Falha de armazenamento: ``status`` HTTP (400 caminho, 403 operação não liberada,
    404 inexistente ou conexão não vinculada, 409, 413, 429/503 transitório, 502 provedor)."""

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


def clean_path(p: str, file: bool = True) -> str:
    """Normaliza um caminho relativo à pasta-base (mesmas regras do hub): sem ``..``/``.``,
    ``\\``, ``%``, ``:*?"<>|#``, controle, sósias Unicode de ``.``/``/``; segmentos sem
    espaço no início nem espaço/ponto no fim; ≤ 255 bytes por segmento, ≤ 32 níveis,
    ≤ 1024 bytes no total."""
    if len(p.encode()) > 1024:
        raise StorageError(400, "Caminho longo demais.")
    segs: list[str] = []
    for seg in p.split("/"):
        if not seg:
            continue
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
        segs.append(seg)
    if len(segs) > 32:
        raise StorageError(400, "Pastas aninhadas demais.")
    out = "/".join(segs)
    if file and not out:
        raise StorageError(400, "Informe o caminho do arquivo.")
    return out


class Download:
    """Corpo em streaming: ``async for chunk in d.chunks()``; ``aclose()`` libera a conexão."""

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
    """Uma conexão de armazenamento (use :func:`connection` ou :func:`s3_direct`)."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.user: str | None = None
        self.user_sig: str | None = None

    @property
    def via_broker(self) -> bool:
        return False

    def for_user(self, x_hub_user: str | None, x_hub_user_sig: str | None = None) -> Connection:
        """Mesma conexão, registrando no hub o usuário da requisição.

        Repasse o ``x-hub-user`` **e** o ``x-hub-user-sig`` (assinatura que o gateway
        injetou): só com a assinatura o hub registra o usuário como autor; sem ela, o autor
        é o app e o usuário aparece como "informado pelo app (não verificado)".
        """
        c = self._copy()
        c.user = x_hub_user or None
        c.user_sig = (x_hub_user_sig or None) if c.user is not None and c.user else None
        return c

    def _copy(self) -> Connection:  # pragma: no cover — sobrescrito
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


class BrokerConnection(Connection):
    """Pelo broker do hub (rede interna do app, token por app × ambiente)."""

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


def _error(status: int, raw: bytes, retry_after: int | None) -> StorageError:
    try:
        msg = str(json.loads(raw).get("error") or "falha no armazenamento")
    except (ValueError, AttributeError):
        msg = "falha no armazenamento"
    if status in (429, 503) and retry_after is None:
        retry_after = 2
    return StorageError(status, msg, retry_after)


def _enc(s: str, keep_slash: bool) -> str:
    return quote(s, safe="/-_.~" if keep_slash else "-_.~")


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    """Chave de assinatura SigV4 (`AWS4` + segredo → data → região → s3 → aws4_request)."""
    key = _hmac(f"AWS4{secret}".encode(), date)
    for part in (region, "s3", "aws4_request"):
        key = _hmac(key, part)
    return key


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str = field(repr=False)
    region: str = "us-east-1"
    base_folder: str = ""
    public_url: str = ""


class S3Connection(Connection):
    """Bucket do app (S3/MinIO) direto, SigV4 com ``UNSIGNED-PAYLOAD``."""

    def __init__(self, name: str, cfg: S3Config) -> None:
        super().__init__(name, "s3")
        cfg.endpoint = cfg.endpoint.rstrip("/")
        if not cfg.endpoint.startswith(("http://", "https://")):
            raise StorageError(400, "Endpoint S3 precisa começar com http:// ou https://.")
        if not re.fullmatch(r"[a-z0-9.\-]{3,63}", cfg.bucket):
            raise StorageError(400, "Bucket inválido.")
        cfg.base_folder = clean_path(cfg.base_folder, file=False)
        self.cfg = cfg
        u = urlsplit(cfg.endpoint)
        self.host = u.netloc

    @override
    def __repr__(self) -> str:
        return f"S3Connection({self.name!r}, {self.cfg.endpoint!r}, {self.cfg.bucket!r})"

    @override
    def _copy(self) -> Connection:
        return S3Connection(self.name, S3Config(**self.cfg.__dict__))

    def _key(self, rel: str) -> str:
        return (
            f"{self.cfg.base_folder}/{rel}" if self.cfg.base_folder and rel else (self.cfg.base_folder or rel)
        )

    def _signed(
        self, method: str, key: str, query: dict[str, str] | None = None, extra: dict[str, str] | None = None
    ) -> tuple[str, dict[str, str]]:
        now = dt.datetime.now(dt.UTC)
        amz, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = f"/{_enc(self.cfg.bucket, False)}" + (f"/{_enc(key, True)}" if key else "")
        q = sorted((_enc(k, False), _enc(v, False)) for k, v in (query or {}).items())
        cq = "&".join(f"{k}={v}" for k, v in q)
        payload = "UNSIGNED-PAYLOAD"
        hdrs = {"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": amz}
        for k, v in (extra or {}).items():
            hdrs[k.lower()] = v.strip()
        names = sorted(hdrs)
        canon = "".join(f"{k}:{hdrs[k]}\n" for k in names)
        signed = ";".join(names)
        creq = f"{method}\n{path}\n{cq}\n{canon}\n{signed}\n{payload}"
        scope = f"{date}/{self.cfg.region}/s3/aws4_request"
        sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(creq.encode()).hexdigest()}"
        sig = hmac.new(
            _signing_key(self.cfg.secret_key, date, self.cfg.region), sts.encode(), hashlib.sha256
        ).hexdigest()
        out = {kk: vv for kk, vv in hdrs.items() if kk != "host"}
        out["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.cfg.access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
        )
        return f"{self.cfg.endpoint}{path}" + (f"?{cq}" if cq else ""), out

    async def _do(self, what: str, method: str, key: str, **kw: Any) -> tuple[Any, Any, bytes | None]:
        url, h = self._signed(method, key, kw.get("query"), kw.get("extra"))
        h.update(kw.get("headers", {}))
        conn, resp = await _call(method, url, h, kw.get("body"), timeout=kw.get("timeout", 60.0))
        if kw.get("stream") and resp.status < 300:
            return conn, resp, None
        try:
            raw = await asyncio.to_thread(resp.read)
        finally:
            conn.close()
        if resp.status >= 300:
            code = {400: 400, 401: 502, 403: 502, 404: 404, 409: 409, 412: 409, 413: 413}.get(resp.status)
            if code is None:
                code = 503 if resp.status in (429, 500, 502, 503, 504) else 502
            raise StorageError(code, f"{what}: object store respondeu {resp.status}", _retry_after(resp))
        return conn, resp, raw

    @override
    async def upload(self, path: str, data: Body, content_type: str, length: int | None = None) -> Item:
        p = clean_path(path)
        n = _length(data, length)
        await self._do(
            "Upload",
            "PUT",
            self._key(p),
            body=data,
            headers={"Content-Type": content_type, "Content-Length": str(n)},
            timeout=600.0,
        )
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return Item(path=p, name=p.rsplit("/", 1)[-1], size=n, modified=now, content_type=content_type)

    @override
    async def download(self, path: str) -> Download:
        p = clean_path(path)
        conn, resp, _ = await self._do("Download", "GET", self._key(p), stream=True)
        n = resp.getheader("content-length")
        return Download(
            resp.getheader("content-type") or "application/octet-stream",
            int(n) if n is not None and n.isdigit() else None,
            resp,
            conn,
        )

    @override
    async def meta(self, path: str) -> Item:
        p = clean_path(path)
        _, resp, _ = await self._do("Metadados", "HEAD", self._key(p))
        n = resp.getheader("content-length")
        return Item(
            path=p,
            name=p.rsplit("/", 1)[-1],
            size=int(n) if n and n.isdigit() else 0,
            modified=resp.getheader("last-modified") or "",
            content_type=resp.getheader("content-type"),
            etag=resp.getheader("etag"),
        )

    @override
    async def list(self, prefix: str = "") -> builtins.list[Item]:
        rel = clean_path(prefix, file=False)
        full = self._key(rel)
        q = {"list-type": "2", "prefix": f"{full}/" if full else "", "delimiter": "/", "max-keys": "1000"}
        _, _, raw = await self._do("Listagem", "GET", "", query=q)
        xml = (raw or b"").decode()
        out: list[Item] = []
        join = (lambda n: f"{rel}/{n}") if rel else (lambda n: n)
        for pre in re.findall(r"<CommonPrefixes><Prefix>(.*?)</Prefix>", xml):
            name = _unxml(pre).rstrip("/").rsplit("/", 1)[-1]
            out.append(Item(path=join(name), name=name, folder=True))
        for c in re.findall(r"<Contents>(.*?)</Contents>", xml, re.S):
            k = re.search(r"<Key>(.*?)</Key>", c)
            size = re.search(r"<Size>(\d+)</Size>", c)
            mod = re.search(r"<LastModified>(.*?)</LastModified>", c)
            if not k or not size:
                continue
            name = _unxml(k.group(1)).rsplit("/", 1)[-1]
            if name:
                out.append(
                    Item(
                        path=join(name),
                        name=name,
                        size=int(size.group(1)),
                        modified=mod.group(1) if mod else "",
                    )
                )
        return out

    @override
    async def delete(self, path: str) -> None:
        p = clean_path(path)
        await self.meta(p)
        await self._do("Exclusão", "DELETE", self._key(p))

    @override
    async def move(self, src: str, dst: str) -> Item:
        s, d = clean_path(src), clean_path(dst)
        await self.meta(s)
        source = f"/{self.cfg.bucket}/{_enc(self._key(s), True)}"
        await self._do("Cópia", "PUT", self._key(d), extra={"x-amz-copy-source": source})
        await self._do("Exclusão da origem", "DELETE", self._key(s))
        return await self.meta(d)

    @override
    async def share_link(self, path: str, kind: str = "view", expires_hours: int = 24) -> ShareLink:
        if kind != "view":
            raise StorageError(400, "No S3 só há link de leitura (view).")
        p = clean_path(path)
        await self.meta(p)
        secs = max(60, min(expires_hours * 3600, 604_800))
        now = dt.datetime.now(dt.UTC)
        exp = (now + dt.timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ShareLink(url=self.presign_get(self._key(p), secs, now), kind="view", expires=exp)

    def presign_get(self, key: str, secs: int, now: dt.datetime) -> str:
        """URL pré-assinada de leitura (mesmo resultado do Rust para as mesmas entradas)."""
        return self.presign("GET", key, secs, now)

    def presign_put_path(self, path: str, secs: int, now: dt.datetime | None = None) -> str:
        """URL pré-assinada de envio (``PUT``) para ``path`` (relativo à pasta-base) —
        envios em duas fases (onda 12, 2.9)."""
        p = clean_path(path)
        return self.presign("PUT", self._key(p), secs, now or dt.datetime.now(dt.UTC))

    def presign(self, method: str, key: str, secs: int, now: dt.datetime) -> str:
        """Assinatura SigV4 por query string (``GET``/``PUT``), igual ao Rust."""
        amz, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        base = (self.cfg.public_url or self.cfg.endpoint).rstrip("/")
        host = urlsplit(base).netloc
        path_enc = f"/{_enc(self.cfg.bucket, False)}/{_enc(key, True)}"
        scope = f"{date}/{self.cfg.region}/s3/aws4_request"
        q = sorted(
            (_enc(k, False), _enc(v, False))
            for k, v in {
                "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                "X-Amz-Credential": f"{self.cfg.access_key}/{scope}",
                "X-Amz-Date": amz,
                "X-Amz-Expires": str(secs),
                "X-Amz-SignedHeaders": "host",
            }.items()
        )
        cq = "&".join(f"{k}={v}" for k, v in q)
        creq = f"{method}\n{path_enc}\n{cq}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
        sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(creq.encode()).hexdigest()}"
        sig = hmac.new(
            _signing_key(self.cfg.secret_key, date, self.cfg.region), sts.encode(), hashlib.sha256
        ).hexdigest()
        return f"{base}{path_enc}?{cq}&X-Amz-Signature={sig}"


def _unxml(s: str) -> str:
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def bound() -> list[tuple[str, str]]:
    """Conexões vinculadas a este app × ambiente (``HUB_CONNECTIONS`` = ``nome=tipo,…``)."""
    out: list[tuple[str, str]] = []
    for part in os.environ.get("HUB_CONNECTIONS", "").split(","):
        name, _, kind = part.strip().partition("=")
        if name and kind:
            out.append((name, kind))
    return out


class LocalConnection(Connection):
    """Pasta local com a mesma API (``hub dev``: ``HUB_STORAGE_LOCAL_DIR``). Sem links de compartilhamento."""

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
        f = open(full, "rb")  # noqa: SIM115 — fechado por Download.aclose
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
    """Conexão ``name`` pelo broker do hub, se vinculada a este app × ambiente
    (no ``hub dev``, ``HUB_STORAGE_LOCAL_DIR`` = pasta local com a mesma API)."""
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
    """Bucket próprio do app (S3/MinIO direto)."""
    return S3Connection(name, cfg)
