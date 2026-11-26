"""What the hub needs to call an app's `/_hub/*` endpoints and to trust the links it sends back: the internal token
and the app's own path."""

from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import unquote

token_source: Callable[[str], str | None] | None = None  # managed vault variable (`AdminStore.tokens`)


def internal_token(app: str) -> str | None:
    """App's `/_hub/*` endpoints token: the managed variable `HUB_INTERNAL_TOKEN_<APP>` (vault); only without it,
    the hub's environment (`HUB_INTERNAL_TOKEN_<APP>`, used as the seed at startup and in dev mode)."""
    key = "HUB_INTERNAL_TOKEN_" + app.upper().replace("-", "_")
    tok = (
        (token_source(app) if token_source else None)
        or os.getenv(key)
        or os.getenv("HUB_INTERNAL_TOKEN")
        or ""
    ).strip()
    return tok if len(tok) >= 16 else None


def safe_app_link(app: str, link: str) -> str:
    """Only the app's own path (`/apps/<id>…`); anything else falls back to the app root."""
    base = f"/apps/{app}"
    if link == f"/{app}" or link.startswith((f"/{app}/", f"/{app}?", f"/{app}#")):
        link = "/apps" + link
    ok = (
        (link == base or link.startswith((f"{base}/", f"{base}?", f"{base}#")))
        and len(link) <= 300
        and "\\" not in link
        and not any(c.isspace() or not c.isprintable() for c in link)
    )
    cur = link.split("?")[0].split("#")[0]
    for _ in range(4):
        low = cur.lower()
        if (
            not ok
            or "//" in cur
            or "%2f" in low
            or "%5c" in low
            or any(s in (".", "..") for s in cur.split("/"))
        ):
            return base
        nxt = unquote(cur)
        if nxt == cur:
            return link
        cur = nxt
    return base
