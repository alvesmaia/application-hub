"""OAuth 2.1 for `/mcp` — constants and pure helpers (URLs, redirect rules, PKCE, names, public IPs)."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
import time
from urllib.parse import parse_qsl, quote

SCOPES = ("mcp", "api:write")
K_CLIENT, K_CODE, K_GRANT, K_REFRESH, K_ACCESS, K_CONSENT = (
    "oauth.client", "oauth.code", "oauth.grant", "oauth.refresh", "oauth.access", "oauth.consent",
)  # fmt: skip
DAY_MS = 86_400_000
# Clients cache the client_id (RFC 7591 has no expiry) and retry the sign-in hours or days later; floods are
# bounded by MAX_UNUSED_CLIENTS and the registration rate limits, not by a short lifetime.
UNUSED_CLIENT_MS, KEPT_CLIENT_MS = 30 * DAY_MS, 3650 * DAY_MS
MAX_UNUSED_CLIENTS, PENDING_CONSENT_RESERVE = 2000, 100
REGISTER_GLOBAL, REGISTER_PER_IP, REGISTER_GLOBAL_WEIGHT = 600, 20, 5
CONSENT_PER_USER, REVOKE_PER_IP, TOKEN_PER_IP = 60, 300, 300
CONSENT_TTL_MS = 10 * 60_000
WINDOW_MS = 15 * 60_000
NO_ISSUER = (
    "OAuth indisponível neste endereço: defina HUB_PUBLIC_URL com o endereço público do hub (sem ela, só "
    "localhost/127.0.0.1 respondem)."
)
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))  # fmt: skip


def enc(s: str) -> str:
    return quote(s, safe="-_.~")


def with_query(base: str, pairs: list[tuple[str, str]]) -> str:
    sep = "&" if "?" in base else "?"
    return base + sep + "&".join(f"{k}={enc(v)}" for k, v in pairs)


def canon(u: str) -> str:
    u = u.strip()
    if "://" not in u:
        return u.rstrip("/")
    scheme, rest = u.split("://", 1)
    cut = next((i for i, c in enumerate(rest) if c in "/?#"), len(rest))
    return f"{scheme.lower()}://{rest[:cut].lower()}{rest[cut:]}".rstrip("/")


def parse_url(u: str) -> tuple[str, str, str | None, str] | None:
    """(scheme, host, port, path+query) or None (no credentials, fragment, spaces or control characters)."""
    if "://" not in u or "#" in u or any(c.isspace() or not c.isprintable() for c in u):
        return None
    scheme, rest = u.split("://", 1)
    cut = next((i for i, c in enumerate(rest) if c in "/?"), len(rest))
    auth, pq = rest[:cut], rest[cut:]
    if not auth or "@" in auth:
        return None
    if auth.startswith("["):
        host, _, tail = auth[1:].partition("]")
        host, port = f"[{host}]", (tail[1:] if tail.startswith(":") else None)
    else:
        host, _, port_s = auth.partition(":")
        port = port_s if ":" in auth else None
    if port is not None and not port.isdigit():
        return None
    return scheme.lower(), host.lower(), port, pq


def host_of(u: str) -> str:
    p = parse_url(u)
    return "" if p is None else f"{p[1]}:{p[2]}" if p[2] else p[1]


def origin_of_url(u: str) -> str | None:
    p = parse_url(u)
    return None if p is None else f"{p[0]}://{p[1]}" + (f":{p[2]}" if p[2] else "")


def redirect_ok(u: str, allowed_hosts: list[str]) -> bool:
    """Acceptable `redirect_uri`: HTTPS (allowed host) or loopback HTTP."""
    p = parse_url(u) if len(u) <= 2000 else None
    if p is None:
        return False
    if p[0] == "http":
        return p[1] in _LOOPBACK
    return p[0] == "https" and (
        p[1] in _LOOPBACK or not allowed_hosts or p[1] in [h.lower() for h in allowed_hosts]
    )


def redirect_matches(registered: str, presented: str) -> bool:
    """Exact match; on loopback the port may vary (RFC 8252 §7.3)."""
    if registered == presented:
        return True
    a, b = parse_url(registered), parse_url(presented)
    return bool(a and b and a[0] == b[0] == "http" and a[1] in _LOOPBACK and a[1] == b[1] and a[3] == b[3]
                and b[2] is not None)  # fmt: skip


def form(s: str) -> dict[str, str]:
    """urlencoded fields; a repeated parameter is an error in OAuth: keeps the first and marks `<k>\\0dup`."""
    m: dict[str, str] = {}
    for k, v in parse_qsl(s, keep_blank_values=True):
        if k in m:
            m[f"{k}\0dup"] = ""
        else:
            m[k] = v
    return m


def _invisible(c: str) -> bool:
    o = ord(c)
    return (not c.isprintable() and not c.isspace()) or o in (0xAD, 0x61C, 0x180E, 0xFEFF) or 0x200B <= o <= 0x200F or 0x2028 <= o <= 0x202E or 0x2060 <= o <= 0x2064 or 0x2066 <= o <= 0x206F or 0xFFF9 <= o <= 0xFFFB or 0xE0000 <= o <= 0xE007F  # fmt: skip


def clean_name(s: str) -> str:
    return "".join(c for c in s if (not _invisible(c) and not c.isspace()) or c == " ")[:80].strip()


def confusable(s: str) -> bool:
    latin = any(c.isascii() and c.isalpha() for c in s)
    other = any(c.isalpha() and not c.isascii() and not 0xC0 <= ord(c) <= 0x24F for c in s)
    return latin and other


def short(s: str) -> str:
    return s[:47] + "…" if len(s) > 48 else s


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128 or not all(
        c.isascii() and (c.isalnum() or c in "-._~") for c in verifier
    ):
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(calc, challenge)


def scope_label(s: str) -> tuple[str, str]:
    return {
        "mcp": ("Usar as ferramentas do hub como você",
                "Ler catálogo, status, implantações, logs e as ferramentas de leitura dos apps que você acessa, com as suas permissões, nunca mais."),
        "api:write": ("Executar ações que alteram algo",
                      "Implantar, reverter, fazer backup, cancelar tarefas e as ações dos apps. Ações destrutivas continuam exigindo a sua aprovação na Inbox, com senha ou login novo."),
    }.get(s, ("", ""))  # fmt: skip


def ip_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip_public(ip.ipv4_mapped)
        b = ip.packed
        if b[:12] == bytes(12):
            return ip_public(ipaddress.IPv4Address(b[12:]))
        if b[:12] == bytes.fromhex("0064ff9b0000000000000000"):
            return ip_public(ipaddress.IPv4Address(b[12:]))
        if b[:2] == b"\x20\x02":
            return ip_public(ipaddress.IPv4Address(b[2:6]))
        s = int(ip) >> 112
        return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or (s & 0xFE00) == 0xFC00
                    or (s & 0xFFC0) in (0xFE80, 0xFEC0) or b[:4] in (b"\x20\x01\x00\x00", b"\x20\x01\x0d\xb8")
                    or (b[:2] == b"\x00\x64" and b[2:4] == b"\xff\x9b"))  # fmt: skip
    o = ip.packed
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.is_reserved or o[0] == 0 or (o[0] == 100 and o[1] & 0xC0 == 64) or (o[0] == 198 and o[1] & 0xFE == 18)
                or o[0] >= 224)  # fmt: skip


class OAuthError(Exception):
    pass
