"""Federated search (hub ⌘K): a process by name → its runs; ``#123`` or ``123`` → that run when
the user may see it (their own; admins: any)."""

from __future__ import annotations

from typing import Any

from hub_sdk.app import HubSdk
from hub_sdk.locale import t
from hub_sdk.search import SearchHit, SearchQuery, score

from automacoes import processes, store
from automacoes.access import is_admin_identity
from automacoes.present import when
from automacoes.state import DB_ERRORS, to_int

BASE = "/apps/automacoes"


async def process_hits(sdk: HubSdk, q: str) -> list[SearchHit]:
    hits = []
    for p in await processes.list_processes(sdk.pool, only_enabled=True):
        s = max(score(p.name, q), score(t(p.name), q), score(p.package_name, q) * 0.8)
        if s > 0:
            url = f"{BASE}/execucoes?processo={p.id}"
            hits.append(SearchHit(t(p.name), url, t("Execuções deste processo"), t("Processo"), s))
    return hits


async def run_hit(sdk: HubSdk, q: SearchQuery) -> list[SearchHit]:
    n = to_int(q.q.strip().removeprefix("#"))
    run = await store.get_run(sdk.pool, n) if n is not None else None
    ident = q.identity
    mine = (
        run is not None
        and ident is not None
        and (is_admin_identity(ident) or run.requested_login == ident.user)
    )
    if run is None or not mine:
        return []
    sub = f"{run.process_name} · {when(run.created_at)}"
    return [SearchHit(t("Execução #{n}", n=run.id), f"{BASE}/execucoes/{run.id}", sub, t("Execução"), 1.0)]


def make_search(sdk: HubSdk) -> Any:
    async def search(q: SearchQuery) -> list[SearchHit]:
        if not sdk.db_ready:
            return []
        try:
            hits = await run_hit(sdk, q) + await process_hits(sdk, q.q)
        except DB_ERRORS:
            return []
        return sorted(hits, key=lambda h: -h.score)[: q.limit]

    return search
