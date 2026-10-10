"""Busca, filtros, ordenação e página da lista (também usados pelas exportações)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ativos.model import CATEGORIES, LOCATIONS, MAX_PAGE, SORTS, STATUSES
from ativos.ui import param, query


def _pick(v: str, opts: tuple[str, ...]) -> str:
    return v if v in opts else ""


@dataclass(frozen=True)
class Filter:
    q: str = ""
    categoria: str = ""
    localizacao: str = ""
    status: str = ""
    sort: str = "nome"
    dir: str = "asc"
    page: int = 1

    @classmethod
    def from_query(cls, q: Mapping[str, str]) -> Filter:
        sort = param(q, "sort")
        try:
            page = int(param(q, "page"))
        except ValueError:
            page = 1
        return cls(
            q=param(q, "q")[:80],
            categoria=_pick(param(q, "categoria"), CATEGORIES),
            localizacao=_pick(param(q, "localizacao"), LOCATIONS),
            status=_pick(param(q, "status"), STATUSES),
            sort=sort if any(s[0] == sort for s in SORTS) else "nome",
            dir="desc" if param(q, "dir") == "desc" else "asc",
            page=max(1, min(page, MAX_PAGE)),
        )

    def where(self) -> tuple[str, list[str]]:
        """``WHERE …`` parametrizado (todos os parâmetros são texto)."""
        conds: list[str] = []
        params: list[str] = []
        if self.q:
            like = "%" + self.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.append(like)
            n = len(params)
            conds.append(f"(name ILIKE ${n} OR tag ILIKE ${n})")
        for col, v in (("category", self.categoria), ("location", self.localizacao), ("status", self.status)):
            if v:
                params.append(v)
                conds.append(f"{col} = ${len(params)}")
        return ("WHERE " + " AND ".join(conds)) if conds else "", params

    def order(self) -> str:
        col = next((s[1] for s in SORTS if s[0] == self.sort), "name")
        d = "DESC" if self.dir == "desc" else "ASC"
        nulls = "NULLS LAST" if d == "DESC" else "NULLS FIRST"
        # Qualificado: a exportação seleciona `col::text`... com o mesmo nome de saída.
        return f"assets.{col} {d} {nulls}, assets.id {d}"

    def qs(self, **over: str) -> str:
        """Query string com os filtros atuais e alterações pontuais (vazio = remove)."""
        pairs: dict[str, str] = {
            "q": self.q,
            "categoria": self.categoria,
            "localizacao": self.localizacao,
            "status": self.status,
            "sort": "" if self.sort == "nome" else self.sort,
            "dir": "" if self.dir == "asc" else self.dir,
            "page": "" if self.page == 1 else str(self.page),
        }
        pairs.update(over)
        return query(list(pairs.items()))

    def with_page(self, p: int) -> Filter:
        return replace(self, page=p)

    def describe(self) -> str:
        parts = []
        if self.q:
            parts.append(f'busca "{self.q}"')
        for k, v in (
            ("categoria", self.categoria),
            ("localização", self.localizacao),
            ("status", self.status),
        ):
            if v:
                parts.append(f"{k} {v}")
        return ", ".join(parts) if parts else "sem filtros"

    def payload(self) -> dict[str, Any]:
        return {
            "filter": {
                "q": self.q,
                "categoria": self.categoria,
                "localizacao": self.localizacao,
                "status": self.status,
                "sort": self.sort,
                "dir": self.dir,
            },
            "describe": self.describe(),
        }
