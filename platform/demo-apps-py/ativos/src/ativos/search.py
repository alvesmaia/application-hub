"""Federated search provider (wave 9): assets by name, asset tag, category or location."""

from __future__ import annotations

import unicodedata
from typing import Any

from hub_sdk import base
from hub_sdk.app import HubSdk
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score

from ativos.ui import query

NOUNS = ("ativo", "ativos", "asset", "assets", "activo", "activos", "patrimonio", "inventario")


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """Is the search the entity name ("ativos", "assets", "patrimônio")? Then the most recent ones
    answer (global search never comes back empty for the app)."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


async def _rows(c: Any, q: SearchQuery, generic: bool) -> list[Any]:
    if generic:
        return await c.fetch(
            "SELECT tag, name, category, location, status FROM assets ORDER BY id DESC LIMIT $1",
            q.limit,
        )
    return await c.fetch(
        "SELECT tag, name, category, location, status FROM assets WHERE name ILIKE $1 OR tag ILIKE $1 "
        "OR category ILIKE $1 OR location ILIKE $1 ORDER BY (tag ILIKE $2) DESC, (name ILIKE $1) DESC, "
        "name LIMIT $3",
        like_pattern(q.q),
        q.q,
        q.limit,
    )


def _score(r: Any, q: SearchQuery, generic: bool) -> float:
    if generic:
        return 0.5
    return max(score(r["name"], q.q), score(r["tag"], q.q), score(r["category"], q.q) * 0.7, 0.3)


def make_search(sdk: HubSdk) -> Any:
    """Federated search (wave 9): name, asset tag, category or location →
    ``/apps/ativos/assets?q=<patrimônio>``, label in the searcher's language."""

    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        generic = generic_term(q.q)
        async with sdk.pool.acquire() as c:
            rows = await _rows(c, q, generic)
        kind = {"en": "Asset"}.get(q.locale, "Ativo")
        return [
            SearchHit(
                title=r["name"],
                subtitle=f"{r['tag']} · {r['category']} · {r['location']}",
                url=f"{base('ativos')}/assets{query([('q', r['tag'])])}",
                kind=kind,
                score=_score(r, q, generic),
            )
            for r in rows
        ]

    return search
