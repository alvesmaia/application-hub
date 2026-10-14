"""Client address behind trusted proxies and the control-plane CIDR guard."""

from __future__ import annotations

import ipaddress
import os

from fastapi import Request


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    # HUB_EDGE_CIDR = edge network (app-hub-edge, from the compose file itself): always trusted, like the declared proxies
    trusted = _cidrs(os.getenv("HUB_TRUSTED_PROXIES", "") + "," + os.getenv("HUB_EDGE_CIDR", ""))
    try:
        source = ipaddress.ip_address(peer)
    except ValueError:
        return "?"
    if not any(source in c for c in trusted):
        return str(source)
    entries = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    for entry in reversed(entries):
        try:
            ip = ipaddress.ip_address(entry)
        except ValueError:
            return "?"
        if not any(ip in c for c in trusted):
            return str(ip)
    return "?"


def _cidrs(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    try:
        return [ipaddress.ip_network(x.strip(), strict=False) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []


def control_allowed(request: Request, mcp_public: bool = False, mcp_path: str = "/mcp") -> bool:
    path = request.url.path
    if mcp_public and (path == mcp_path or path.startswith(("/.well-known/", "/oauth/"))):
        return True
    segs = path.strip("/").split("/")
    # /oauth/authorize is a person-facing screen (like /account); the rest of /oauth follows the control plane.
    if segs[0] not in ("api", "admin", "mcp", ".well-known", "oauth") or path == "/oauth/authorize":
        return True
    raw = os.getenv("HUB_ADMIN_ALLOWED_CIDRS")
    if raw is None:
        return True
    try:
        ip = ipaddress.ip_address(client_ip(request))
        return any(ip in c for c in _cidrs(raw))
    except ValueError:
        return False
