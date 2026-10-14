"""Federated search provider (wave 9): tickets by reference or title."""

from __future__ import annotations

import unicodedata
from typing import Any

from hub_sdk.app import HubSdk
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score

from servicos.ui import url_enc

NOUNS = (
    "chamado",
    "chamados",
    "solicitacao",
    "solicitacoes",
    "ticket",
    "tickets",
    "request",
    "requests",
    "solicitud",
)


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """Is the search the entity name ("chamados", "ticket")? Then the most recent ones answer."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


async def _rows(c: Any, q: SearchQuery, exact: str, generic: bool) -> list[Any]:
    if generic:
        return await c.fetch(
            "SELECT protocol, title, status, category FROM tickets ORDER BY id DESC LIMIT $1", q.limit
        )
    return await c.fetch(
        "(SELECT protocol, title, status, category FROM tickets WHERE protocol = $1) UNION "
        "(SELECT protocol, title, status, category FROM (SELECT protocol, title, status, category "
        "FROM tickets ORDER BY id DESC LIMIT 20000) r WHERE title ILIKE $2 OR protocol ILIKE $2 LIMIT $3)",
        exact,
        like_pattern(q.q),
        q.limit,
    )


def _score(r: Any, q: SearchQuery, exact: str, generic: bool) -> float:
    if r["protocol"] == exact:
        return 1.0
    if generic:
        return 0.5
    return max(score(r["title"], q.q), score(r["protocol"], q.q), 0.3)


def make_search(sdk: HubSdk) -> Any:
    """Federated search (wave 9): exact reference (unique index) + title/reference across the 20k most
    recent tickets → ``/apps/servicos/requests?protocolo=<protocolo>``."""

    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        exact = q.q.upper()
        generic = generic_term(q.q)
        async with sdk.pool.acquire() as c:
            rows = await _rows(c, q, exact, generic)
        kind = {"en": "Ticket"}.get(q.locale, "Chamado")
        return [
            SearchHit(
                title=r["title"],
                subtitle=f"{r['protocol']} · {r['status']} · {r['category']}",
                url=f"/apps/servicos/requests?protocolo={url_enc(r['protocol'])}",
                kind=kind,
                score=_score(r, q, exact, generic),
            )
            for r in rows
        ]

    return search
