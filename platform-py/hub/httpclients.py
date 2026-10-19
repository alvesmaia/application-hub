"""HTTP clients of the hub. Internal calls (apps, MinIO, the hub itself) never go through a proxy; external ones
(Entra, SharePoint, S3, webhooks, update check, OTLP) use HUB_PROXY, or the standard *_PROXY variables, except for
HUB_NO_PROXY/NO_PROXY destinations, hosts without a dot (compose services, containers) and private addresses.
The corporate CA (install.sh --ca-file, mounted at /config/ca/ca.pem) is trusted on top of the public ones.

The clients never read the proxy from the environment by themselves (trust_env=False): docker injects HTTP_PROXY into
every container, and an internal call that follows it ends up at the corporate proxy."""

from __future__ import annotations

import ipaddress
import os
import ssl
from functools import lru_cache
from typing import Any, override

import certifi
import httpx

CA_FILE = "/config/ca/ca.pem"


def proxy_url() -> str | None:
    for k in ("HUB_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if v := os.getenv(k, "").strip():
            return v
    return None


def no_proxy() -> list[str]:
    raw = ",".join(os.getenv(k, "") for k in ("HUB_NO_PROXY", "NO_PROXY", "no_proxy"))
    return list(dict.fromkeys(e.strip() for e in raw.split(",") if e.strip()))


def _ip_bypass(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, rules: list[str]) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    for rule in rules:
        try:
            if ip in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            continue
    return False


def bypass(host: str, rules: list[str]) -> bool:
    """True = connect directly: no dot, a private IP, or a HUB_NO_PROXY match (`*`, CIDR, domain and its subdomains)."""
    host = host.strip("[]").lower()
    try:
        return _ip_bypass(ipaddress.ip_address(host), rules)
    except ValueError:
        pass
    if "." not in host or "*" in rules:
        return True
    return any(
        host == r.lstrip(".").lower() or host.endswith("." + r.lstrip(".").lower()) for r in rules if r
    )


@lru_cache(maxsize=4)
def _context(extra: str, mtime: float) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    if mtime:
        ctx.load_verify_locations(cafile=extra)
    return ctx


def ssl_context() -> ssl.SSLContext:
    extra = os.getenv("HUB_EXTRA_CA_FILE", CA_FILE)
    try:
        mtime = os.stat(extra).st_mtime
    except OSError:
        mtime = 0.0
    return _context(extra, mtime)


class AsyncRouting(httpx.AsyncBaseTransport):
    """Per request: the proxied transport for external hosts, the direct one for the rest."""

    def __init__(
        self, direct: httpx.AsyncBaseTransport, proxied: httpx.AsyncBaseTransport | None, rules: list[str]
    ) -> None:
        self.direct, self.proxied, self.rules = direct, proxied, rules

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        t = self.direct if self.proxied is None or bypass(request.url.host, self.rules) else self.proxied
        return await t.handle_async_request(request)

    @override
    async def aclose(self) -> None:
        await self.direct.aclose()
        if self.proxied is not None:
            await self.proxied.aclose()


class SyncRouting(httpx.BaseTransport):
    def __init__(
        self, direct: httpx.BaseTransport, proxied: httpx.BaseTransport | None, rules: list[str]
    ) -> None:
        self.direct, self.proxied, self.rules = direct, proxied, rules

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        t = self.direct if self.proxied is None or bypass(request.url.host, self.rules) else self.proxied
        return t.handle_request(request)

    @override
    def close(self) -> None:
        self.direct.close()
        if self.proxied is not None:
            self.proxied.close()


def _async_routing() -> AsyncRouting:
    ctx, proxy = ssl_context(), proxy_url()
    proxied = httpx.AsyncHTTPTransport(verify=ctx, proxy=proxy) if proxy else None
    return AsyncRouting(httpx.AsyncHTTPTransport(verify=ctx), proxied, no_proxy())


def internal_client(**kw: Any) -> httpx.AsyncClient:
    """Apps and the hub's own services: always direct."""
    return httpx.AsyncClient(trust_env=False, **kw)


def external_client(transport: httpx.AsyncBaseTransport | None = None, **kw: Any) -> httpx.AsyncClient:
    """Destinations configured by the operator or on the internet; `transport` is for tests."""
    return httpx.AsyncClient(transport=transport or _async_routing(), trust_env=False, **kw)


def external_sync_client(transport: httpx.BaseTransport | None = None, **kw: Any) -> httpx.Client:
    if transport is None:
        ctx, proxy = ssl_context(), proxy_url()
        proxied = httpx.HTTPTransport(verify=ctx, proxy=proxy) if proxy else None
        transport = SyncRouting(httpx.HTTPTransport(verify=ctx), proxied, no_proxy())
    return httpx.Client(transport=transport, trust_env=False, **kw)
