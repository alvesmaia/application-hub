"""Paginação no servidor (`?page=&per=`) das tabelas que crescem; o seletor vem de `_pager.html`."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import Request

PER_CHOICES = (10, 25, 50, 100)


def paginate(request: Request, rows: list[Any], per: int = 25) -> tuple[list[Any], dict[str, Any]]:
    q = request.query_params

    def num(k: str, d: int) -> int:
        v = q.get(k, "")
        return int(v) if v.isdigit() and int(v) > 0 else d

    per = min(num("per", per), 100)
    pages = max(1, -(-len(rows) // per))
    page = min(num("page", 1), pages)
    keep = [(k, v) for k, v in q.multi_items() if k not in ("page", "per")]

    def href(p: int) -> str:
        return "?" + urlencode([*keep, ("page", p), ("per", per)])

    info = {
        "page": page,
        "pages": pages,
        "per": per,
        "total": len(rows),
        "first": (page - 1) * per + 1 if rows else 0,
        "last": min(page * per, len(rows)),
        "prev": href(page - 1) if page > 1 else "",
        "next": href(page + 1) if page < pages else "",
        "keep": keep,
        "choices": PER_CHOICES,
    }
    return rows[(page - 1) * per : page * per], info
