"""Cliente S3/MinIO do broker: SigV4 com `UNSIGNED-PAYLOAD` e URL pré-assinada (vetores em `hub-storage/vectors.json`)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import os
import random
import re
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from hub.storage import Body, Item, Steps, StorageError, clean, clean_file, join, rfc3339

_FAIL = {
    400: ("invalid", "pedido recusado"),
    401: ("auth", "credencial recusada pelo object store"),
    403: ("auth", "credencial recusada pelo object store"),
    404: ("not_found", "não encontrado"),
    409: ("conflict", "conflito"),
    412: ("conflict", "conflito"),
    413: ("too_large", "arquivo grande demais"),
}


BLOCKED_MSG = (
    "Endpoint S3 aponta para um endereço bloqueado (link-local/metadados de nuvem, ex. 169.254.169.254). "
    "Se for mesmo o destino, libere o host em HUB_STORAGE_ALLOW_HOSTS."
)


def blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Endereços que o endpoint nunca alcança (SSRF para metadados de nuvem); redes privadas e loopback seguem."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return blocked_ip(ip.ipv4_mapped)
        s0 = ip.packed[0] << 8 | ip.packed[1]
        return ip.is_unspecified or (s0 & 0xFFC0) == 0xFE80 or (s0 & 0xFF00) == 0xFD00
    o = ip.packed
    return (
        o[0] == 0
        or (o[0] == 169 and o[1] == 254)
        or o == bytes([100, 100, 100, 200])
        or o == bytes([255]) * 4
    )


def endpoint_allowed(host: str) -> bool:
    allow = os.getenv("HUB_STORAGE_ALLOW_HOSTS", "")
    return host.strip("[]").lower() in {h.strip().strip("[]").lower() for h in allow.split(",") if h.strip()}


def _enc(s: str, keep_slash: bool) -> str:
    return quote(s, safe="/-_.~" if keep_slash else "-_.~")


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), date)
    for part in (region, "s3", "aws4_request"):
        key = _hmac(key, part)
    return key


def _unxml(s: str) -> str:
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


@dataclass
class S3Config:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str = field(repr=False)
    region: str = "us-east-1"
    base_folder: str = ""
    public_url: str = ""


class S3Client:
    def __init__(self, cfg: S3Config, transport: httpx.AsyncBaseTransport | None = None) -> None:
        cfg.endpoint = cfg.endpoint.rstrip("/")
        if not cfg.endpoint.startswith(("http://", "https://")):
            raise StorageError("invalid", "Endpoint S3 precisa começar com http:// ou https://.")
        if not re.fullmatch(r"[a-z0-9.\-]{3,63}", cfg.bucket):
            raise StorageError("invalid", "Bucket inválido.")
        host = (urlsplit(cfg.endpoint).hostname or "").lower()
        if not endpoint_allowed(host):
            try:
                if blocked_ip(ipaddress.ip_address(host)):
                    raise StorageError("invalid", BLOCKED_MSG)
            except ValueError:
                pass
        self.cfg, self.host = cfg, urlsplit(cfg.endpoint).netloc
        self.http = httpx.AsyncClient(transport=transport, timeout=60.0)

    def _key(self, rel: str) -> str:
        return join(self.cfg.base_folder, rel)

    def _signed(
        self, method: str, key: str, query: dict[str, str] | None = None, extra: dict[str, str] | None = None
    ) -> tuple[str, dict[str, str]]:
        now = datetime.now(UTC)
        amz, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = f"/{_enc(self.cfg.bucket, False)}" + (f"/{_enc(key, True)}" if key else "")
        pairs = sorted((_enc(k, False), _enc(v, False)) for k, v in (query or {}).items())
        cq = "&".join(f"{k}={v}" for k, v in pairs)
        payload = "UNSIGNED-PAYLOAD"
        hdrs = {"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": amz}
        hdrs |= {k.lower(): v.strip() for k, v in (extra or {}).items()}
        names = sorted(hdrs)
        signed = ";".join(names)
        canon = "".join(f"{k}:{hdrs[k]}\n" for k in names)
        creq = f"{method}\n{path}\n{cq}\n{canon}\n{signed}\n{payload}"
        scope = f"{date}/{self.cfg.region}/s3/aws4_request"
        sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(creq.encode()).hexdigest()}"
        sig = hmac.new(_signing_key(self.cfg.secret_key, date, self.cfg.region), sts.encode(), hashlib.sha256)
        out = {k: v for k, v in hdrs.items() if k != "host"}
        out["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.cfg.access_key}/{scope}, SignedHeaders={signed}, Signature={sig.hexdigest()}"
        )
        return f"{self.cfg.endpoint}{path}" + (f"?{cq}" if cq else ""), out

    async def _do(
        self,
        what: str,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        extra: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        content: Any = None,
        stream: bool = False,
    ) -> httpx.Response:
        url, h = self._signed(method, key, query, extra)
        h |= headers or {}
        try:
            req = self.http.build_request(method, url, headers=h, content=content)
            resp = await self.http.send(req, stream=stream)
        except httpx.HTTPError as e:
            raise StorageError("unavailable", f"{what}: object store inacessível", 2) from e
        if resp.status_code < 300:
            return resp
        await resp.aclose()
        n = resp.status_code
        if n in (429, 500, 502, 503, 504):
            ra = resp.headers.get("retry-after", "")
            raise StorageError(
                "unavailable",
                f"{what}: object store indisponível (HTTP {n})",
                int(ra) if ra.isdigit() else None,
            )
        kind, msg = _FAIL.get(n, ("upstream", "resposta inesperada"))
        raise StorageError(kind, f"{what}: {msg} (HTTP {n})")

    async def aclose(self) -> None:
        await self.http.aclose()

    async def check_endpoint(self) -> None:
        """Confere (no teste) se o endpoint resolve para um endereço permitido."""
        u = urlsplit(self.cfg.endpoint)
        host = u.hostname or ""
        if endpoint_allowed(host):
            return
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM)
        except OSError as e:
            raise StorageError("unavailable", f"Endpoint S3: não foi possível resolver {host}.") from e
        ips = [ipaddress.ip_address(i[4][0]) for i in infos]
        if ips and all(blocked_ip(ip) for ip in ips):
            raise StorageError("invalid", BLOCKED_MSG)

    async def meta(self, rel: str) -> Item:
        p = clean_file(rel)
        r = await self._do("Metadados", "HEAD", self._key(p))
        n = r.headers.get("content-length", "")
        return Item(
            path=p,
            name=p.rsplit("/", 1)[-1],
            size=int(n) if n.isdigit() else 0,
            modified=r.headers.get("last-modified", ""),
            content_type=r.headers.get("content-type"),
            etag=r.headers.get("etag"),
        )

    async def ls(self, prefix: str = "") -> list[Item]:
        rel = clean(prefix)
        full = self._key(rel)
        q = {"list-type": "2", "prefix": f"{full}/" if full else "", "delimiter": "/", "max-keys": "1000"}
        xml = (await self._do("Listagem", "GET", "", query=q)).text
        out: list[Item] = []
        for pre in re.findall(r"<CommonPrefixes><Prefix>(.*?)</Prefix>", xml):
            name = _unxml(pre).rstrip("/").rsplit("/", 1)[-1]
            out.append(Item(path=join(rel, name), name=name, folder=True))
        for c in re.findall(r"<Contents>(.*?)</Contents>", xml, re.S):
            k, size, mod = (re.search(f"<{t}>(.*?)</{t}>", c) for t in ("Key", "Size", "LastModified"))
            name = _unxml(k.group(1)).rsplit("/", 1)[-1] if k else ""
            if k and size and name:
                out.append(
                    Item(
                        path=join(rel, name),
                        name=name,
                        size=int(size.group(1)),
                        modified=mod.group(1) if mod else "",
                    )
                )
        return out

    async def download(self, rel: str) -> tuple[str, int | None, AsyncIterator[bytes]]:
        p = clean_file(rel)
        r = await self._do("Download", "GET", self._key(p), stream=True)
        n = r.headers.get("content-length", "")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()

        ctype = r.headers.get("content-type", "application/octet-stream")
        return ctype, int(n) if n.isdigit() else None, body()

    async def upload(self, rel: str, length: int, content_type: str, body: Body) -> Item:
        p = clean_file(rel)
        headers = {"Content-Type": content_type, "Content-Length": str(length)}
        await self._do("Upload", "PUT", self._key(p), headers=headers, content=body)
        return Item(
            path=p,
            name=p.rsplit("/", 1)[-1],
            size=length,
            modified=rfc3339(time.time()),
            content_type=content_type,
        )

    async def delete(self, rel: str) -> None:
        p = clean_file(rel)
        await self.meta(p)
        await self._do("Exclusão", "DELETE", self._key(p))

    async def move(self, src: str, dst: str) -> Item:
        s, d = clean_file(src), clean_file(dst)
        await self.meta(s)
        source = f"/{self.cfg.bucket}/{_enc(self._key(s), True)}"
        await self._do("Cópia", "PUT", self._key(d), extra={"x-amz-copy-source": source})
        await self._do("Exclusão da origem", "DELETE", self._key(s))
        return await self.meta(d)

    async def share_link(self, rel: str, kind: str, scope: str, expires_s: int | None) -> dict[str, Any]:
        if kind != "view":
            raise StorageError("invalid", "No S3 só há link de leitura (view).")
        p = clean_file(rel)
        await self.meta(p)
        secs = max(60, min(expires_s or 86_400, 604_800))
        now = datetime.now(UTC)
        return {
            "url": self.presign("GET", self._key(p), secs, now),
            "kind": "view",
            "expires": rfc3339(now.timestamp() + secs),
        }

    def presign(self, method: str, key: str, secs: int, now: datetime) -> str:
        amz, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        base = (self.cfg.public_url or self.cfg.endpoint).rstrip("/")
        path_enc = f"/{_enc(self.cfg.bucket, False)}/{_enc(key, True)}"
        scope = f"{date}/{self.cfg.region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.cfg.access_key}/{scope}",
            "X-Amz-Date": amz,
            "X-Amz-Expires": str(secs),
            "X-Amz-SignedHeaders": "host",
        }
        cq = "&".join(
            f"{k}={v}" for k, v in sorted((_enc(k, False), _enc(v, False)) for k, v in params.items())
        )
        creq = f"{method}\n{path_enc}\n{cq}\nhost:{urlsplit(base).netloc}\n\nhost\nUNSIGNED-PAYLOAD"
        sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(creq.encode()).hexdigest()}"
        sig = hmac.new(_signing_key(self.cfg.secret_key, date, self.cfg.region), sts.encode(), hashlib.sha256)
        return f"{base}{path_enc}?{cq}&X-Amz-Signature={sig.hexdigest()}"

    async def test(self) -> list[dict[str, Any]]:
        s = Steps()

        async def bucket() -> tuple[None, str]:
            await self.check_endpoint()
            await self._do("Bucket", "GET", "", query={"list-type": "2", "max-keys": "1"})
            return None, f'Bucket "{self.cfg.bucket}" acessível.'

        await s.run("bucket", bucket)
        probe = f".hub-sonda-{random.randrange(16**8):08x}.txt"  # noqa: S311
        data = bytes(random.choices(b"abcdefghijklmnopqrstuvwxyz0123456789", k=64))  # noqa: S311

        async def feed() -> AsyncIterator[bytes]:
            yield data

        async def write() -> tuple[None, str]:
            await self.upload(probe, len(data), "text/plain", Body(feed()))
            return None, f"Arquivo de sonda {probe} gravado."

        async def read() -> tuple[None, str]:
            _, _, stream = await self.download(probe)
            if b"".join([c async for c in stream]) != data:
                raise StorageError("upstream", "O conteúdo lido difere do gravado.")
            return None, "Conteúdo lido e conferido."

        wrote = await s.run("write", write)
        await s.run("read", read)
        if wrote:
            try:
                await self.delete(probe)
                s.steps.append({"key": "delete", "ok": True, "detail": "Arquivo de sonda apagado.", "ms": 0})
            except StorageError as e:
                s.steps.append({"key": "delete", "ok": False, "detail": e.message, "ms": 0})
        else:
            s.steps.append({"key": "delete", "ok": None, "detail": "", "ms": 0})
        return s.steps
