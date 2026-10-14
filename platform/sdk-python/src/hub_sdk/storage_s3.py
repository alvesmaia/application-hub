"""Direct S3/MinIO backend of :mod:`hub_sdk.storage` (SigV4 with ``UNSIGNED-PAYLOAD``, presigned URLs)."""

from __future__ import annotations

import asyncio
import builtins
import datetime as dt
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from typing_extensions import override

from hub_sdk.storage_core import (
    Body,
    Connection,
    Download,
    Item,
    ShareLink,
    StorageError,
    _call,
    _length,
    _retry_after,
    clean_path,
)


def _enc(s: str, keep_slash: bool) -> str:
    return quote(s, safe="/-_.~" if keep_slash else "-_.~")


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str) -> bytes:
    """SigV4 signing key (`AWS4` + secret → date → region → s3 → aws4_request)."""
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
    """App's bucket (S3/MinIO) direct, SigV4 with ``UNSIGNED-PAYLOAD``."""

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
        """Pre-signed read URL."""
        return self.presign("GET", key, secs, now)

    def presign_put_path(self, path: str, secs: int, now: dt.datetime | None = None) -> str:
        """Pre-signed upload URL (``PUT``) for ``path`` (relative to the base folder) —
        two-phase uploads (wave 12, 2.9)."""
        p = clean_path(path)
        return self.presign("PUT", self._key(p), secs, now or dt.datetime.now(dt.UTC))

    def presign(self, method: str, key: str, secs: int, now: dt.datetime) -> str:
        """SigV4 signature in the query string (``GET``/``PUT``)."""
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
