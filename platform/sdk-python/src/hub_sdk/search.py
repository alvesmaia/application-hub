"""Busca federada (onda 9): ``GET /{app}/_hub/search?q=&limit=``.

The hub calls this endpoint over the internal network (internal token) with the identity of whoever is searching
(``x-hub-user``, ``x-hub-locale``); the app returns only what that person can see. The SDK
validates the query (1–100 characters), caps ``limit`` at :data:`MAX_LIMIT`, trims the fields
and discards results whose ``url`` is not of the app itself.

Resposta: ``{"results": [{"title", "subtitle", "url", "kind", "score"}]}`` (score 0–1,
maior primeiro).
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from hub_sdk.identity import Identity
from hub_sdk.schema import APPS_PREFIX

MAX_LIMIT = 20
MAX_QUERY = 100


@dataclass(frozen=True)
class SearchQuery:
    q: str
    limit: int
    identity: Identity | None
    locale: str


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    subtitle: str = ""
    kind: str = ""
    score: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


SearchFn = Callable[[SearchQuery], Awaitable[list[SearchHit]]]


def _cut(s: str, n: int) -> str:
    return "".join(c for c in s if c.isprintable())[:n]


def url_in_app(app: str, url: str) -> bool:
    # Wave 13: public URL ``/apps/{app}…`` (the old ``/{app}…`` is still accepted).
    if url.startswith(APPS_PREFIX + "/"):
        url = url[len(APPS_PREFIX) :]
    base = f"/{app}"
    ok = url == base or url.startswith((f"{base}/", f"{base}?"))
    return (
        ok
        and ".." not in url
        and "\\" not in url
        and not any(c.isspace() or not c.isprintable() for c in url)
    )


def sanitize(app: str, hits: list[SearchHit], limit: int) -> list[SearchHit]:
    out = [
        SearchHit(
            title=_cut(h.title, 120),
            subtitle=_cut(h.subtitle, 200),
            url=_cut(h.url, 300),
            kind=_cut(h.kind, 40),
            score=min(1.0, max(0.0, h.score)) if math.isfinite(h.score) else 0.0,
        )
        for h in hits
        if url_in_app(app, h.url) and h.title.strip()
    ]
    out.sort(key=lambda h: -h.score)
    return out[: max(1, min(limit, MAX_LIMIT))]


def fold(s: str) -> str:
    """Lowercase, no accents."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def score(text: str, q: str) -> float:
    """Equal → 1, starts with → 0.8, word starts with → 0.6, contains → 0.4, otherwise 0."""
    t, n = fold(text), fold(q.strip())
    if not n:
        return 0.0
    if t == n:
        return 1.0
    if t.startswith(n):
        return 0.8
    if any(w.startswith(n) for w in "".join(c if c.isalnum() else " " for c in t).split()):
        return 0.6
    return 0.4 if n in t else 0.0


def like_pattern(q: str) -> str:
    """Safe ``ILIKE`` pattern (escapes ``%``, ``_`` and ``\\``)."""
    return "%" + "".join("\\" + c if c in "%_\\" else c for c in q) + "%"
