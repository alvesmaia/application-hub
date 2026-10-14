"""Federated search provider: catalog items by code, name or SKU."""

from __future__ import annotations

from typing import Any

from hub_sdk.app import HubSdk
from hub_sdk.locale import t
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score

from coletor.model import normalize_code


def make_search(sdk: HubSdk) -> Any:
    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        code = normalize_code(q.q)
        async with sdk.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT code, code128, name, category FROM col_items "
                "WHERE code = $2 OR code128 = $2 OR name ILIKE $1 OR sku ILIKE $1 "
                "ORDER BY (code = $2 OR code128 = $2) DESC, name LIMIT $3",
                like_pattern(q.q),
                code,
                q.limit,
            )
        return [
            SearchHit(
                title=r["name"],
                subtitle=f"{r['code']} · {r['code128']} · {t(r['category'])}",
                url=f"/apps/coletor/escanear?codigo={r['code']}",
                kind=t("Item"),
                score=1.0 if code in (r["code"], r["code128"]) else max(score(r["name"], q.q), 0.3),
            )
            for r in rows
        ]

    return search
