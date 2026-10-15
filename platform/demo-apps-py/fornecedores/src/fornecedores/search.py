"""Federated search provider: the supplier directory."""

from __future__ import annotations

import unicodedata

from hub_sdk import base
from hub_sdk.search import SearchHit, SearchQuery, score

from fornecedores.suppliers import SUPPLIERS, Supplier

NOUNS = ("fornecedor", "fornecedores", "supplier", "suppliers", "proveedor", "proveedores")


def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """Is the search the entity name ("fornecedores", "suppliers")? Then all of them answer."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


def _score(s: Supplier, q: str, generic: bool) -> float:
    return max(
        0.5 if generic else 0.0,
        score(s.name, q),
        score(s.code, q),
        score(s.category, q) * 0.7,
        score(s.city, q) * 0.6,
    )


def search_hits(q: SearchQuery) -> list[SearchHit]:
    kind = {"en": "Supplier"}.get(q.locale, "Fornecedor")
    generic = generic_term(q.q)
    hits = []
    for s in SUPPLIERS:
        sc = _score(s, q.q, generic)
        if sc > 0:
            hits.append(
                SearchHit(
                    title=s.name,
                    subtitle=f"{s.code} · {s.category} · {s.city}",
                    url=f"{base('fornecedores')}/indicators?id={s.code}",
                    kind=kind,
                    score=sc,
                )
            )
    return hits


async def search(q: SearchQuery) -> list[SearchHit]:
    return search_hits(q)
