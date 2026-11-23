"""Runs: execute from the generated form (modal), history with filters (own runs; admins: all),
detail (polls while not terminal), re-run and cancel."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.locale import t

from automacoes import packages, present, processes, store, views
from automacoes.access import is_admin, visible_login
from automacoes.fields import validate
from automacoes.model import STATUSES, Run
from automacoes.state import (
    DB_ERRORS,
    State,
    StateDep,
    mark_committed,
    may_see,
    new_run,
    see_other,
    to_int,
    user_of,
)

router = APIRouter()


async def params_form(req: Request) -> dict[str, str]:
    """The package's fields of a posted form (inputs named ``p_<field>``)."""
    form = await req.form()
    return {k[2:]: str(v) for k, v in form.items() if k.startswith("p_")}


@router.post("/automacoes/catalogo/{pid}/executar")
async def execute(pid: str, req: Request, st: StateDep) -> Response:
    if (n := st.db_notice()) is not None:
        return st.page(req, "aviso.html", "catalogo", 503, notice_html=n)
    a = await views.runnable(st.pool, to_int(pid), is_admin(req))
    if a is None or a.process is None:
        return st.render.not_found()
    raw = await params_form(req)
    values, errors = validate(a.fields, raw)
    if errors:
        return st.fragment(req, "_run_form.html", 422, a=a, values=raw, errors=errors)
    run = await new_run(st, req, a.process, dict(values), "manual")
    return see_other(f"/execucoes/{run.id}")


def run_filter(q: Mapping[str, str], login: str) -> store.RunFilter:
    """Lenient filters: an unknown value falls back to "all" (30 days, page 1)."""
    days = to_int(q.get("periodo", "")) or 30
    return store.RunFilter(
        process_id=to_int(q.get("processo", "")) or 0,
        status=q.get("status", "") if q.get("status", "") in STATUSES else "",
        days=days if days in store.PERIODS else 30,
        page=to_int(q.get("pagina", "")) or 1,
        login=login,
    )


def page_link(f: store.RunFilter, page: int) -> str:
    q = {"processo": str(f.process_id or ""), "status": f.status, "periodo": str(f.days), "pagina": str(page)}
    return "?" + urlencode({k: v for k, v in q.items() if v})


@router.get("/automacoes/execucoes")
async def history(req: Request, st: StateDep) -> Response:
    f = run_filter(req.query_params, visible_login(req))
    total, items, names, notice = 0, [], {}, st.db_notice()
    if notice is None:
        try:
            total, items = await store.list_runs(st.pool, f)
            names = {p.id: p.name for p in await processes.list_processes(st.pool, only_enabled=False)}
        except DB_ERRORS as e:
            notice = st.render.notice("error", t("Erro ao consultar as execuções"), str(e))
    pages = max(1, -(-total // store.PAGE_SIZE))
    links = {"prev": page_link(f, f.page - 1) if f.page > 1 else "",
             "next": page_link(f, f.page + 1) if f.page < pages else ""}  # fmt: skip
    return st.page(req, "execucoes.html", "execucoes", f=f, total=total, items=items, pages=pages, links=links,
                   names=names, statuses=STATUSES, periods=store.PERIODS, notice_html=notice)  # fmt: skip


async def run_view(st: State, run: Run) -> dict[str, Any]:
    pkg = await packages.get_package(st.pool, run.package_name, run.package_version)
    label_of = pkg.label_for if pkg is not None else present.humanize
    return {
        "run": run,
        "params_rows": present.rows(run.params, label_of),
        "output_rows": present.rows(run.output or {}, label_of),
    }


async def load(st: State, req: Request, rid: str) -> Run | None:
    """The run if it exists and this user may see it (their own; admins: any)."""
    n = to_int(rid)
    if n is None or not st.db_ok():
        return None
    run = await store.get_run(st.pool, n)
    return run if run is not None and may_see(req, run) else None


@router.get("/automacoes/execucoes/{rid}")
async def detail(rid: str, req: Request, st: StateDep) -> Response:
    if (n := st.db_notice()) is not None:
        return st.page(req, "aviso.html", "execucoes", 503, notice_html=n)
    run = await load(st, req, rid)
    if run is None:
        return st.render.not_found()
    return st.page(req, "execucao.html", "execucoes", **await run_view(st, run))


@router.get("/automacoes/execucoes/{rid}/estado")
async def detail_body(rid: str, req: Request, st: StateDep) -> Response:
    """Polled by the detail page while the run is not terminal."""
    run = await load(st, req, rid)
    if run is None:
        return st.render.not_found()
    return st.fragment(req, "_run_body.html", **await run_view(st, run))


@router.post("/automacoes/execucoes/{rid}/reexecutar")
async def rerun(rid: str, req: Request, st: StateDep) -> Response:
    """A NEW run with the same parameters, on the process's CURRENT version."""
    run = await load(st, req, rid)
    a = await views.runnable(st.pool, run.process_id, is_admin(req)) if run is not None else None
    if run is None or a is None or a.process is None:
        return st.render.not_found()
    new = await new_run(st, req, a.process, run.params, "manual")
    return see_other(f"/execucoes/{new.id}")


@router.post("/automacoes/execucoes/{rid}/cancelar")
async def cancel(rid: str, req: Request, st: StateDep) -> Response:
    """Only while ``pendente``/``processando``; on a terminal run it changes nothing."""
    run = await load(st, req, rid)
    if run is None:
        return st.render.not_found()
    async with st.pool.acquire() as c, c.transaction():
        cancelled = await store.cancel_run(c, run, user_of(req))
        await mark_committed(c, req)
    if cancelled:
        await store.stop_job(st.sdk.jobs, run)
    return see_other(f"/execucoes/{run.id}")
