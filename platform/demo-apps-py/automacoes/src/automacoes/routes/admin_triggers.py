"""Triggers (admins): list, new/edit (choose the process first — the select reloads the form with
its package's fields — then the rule and the parameters), enable/disable and delete."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.locale import t

from automacoes import triggers, views
from automacoes.access import is_admin
from automacoes.fields import validate
from automacoes.model import Trigger
from automacoes.schedule_rules import describe, parse_schedule
from automacoes.state import DB_ERRORS, State, StateDep, mark_committed, see_other, to_int, user_of

router = APIRouter()


def now() -> datetime:
    return datetime.now(UTC)


@router.get("/automacoes/gatilhos")
async def listing(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    items, notice = [], st.db_notice()
    if notice is None:
        try:
            items = [
                {"g": g, "rule": describe(triggers.rule_of(g))} for g in await triggers.list_triggers(st.pool)
            ]
        except DB_ERRORS as e:
            notice = st.render.notice("error", t("Erro ao consultar os gatilhos"), str(e))
    return st.page(req, "gatilhos.html", "gatilhos", items=items, notice_html=notice)


async def form_page(st: State, req: Request, status: int = 200, **ctx: Any) -> Response:
    """``ctx``: ``a`` (form spec of the chosen process), ``trigger``, ``values``, ``errors``, ``rule``,
    ``rule_errors``."""
    for k, v in (("trigger", None), ("errors", {}), ("rule_errors", {}), ("values", {})):
        ctx.setdefault(k, v)
    choices = await views.catalog(st.pool) if st.db_ok() else []
    return st.page(req, "gatilho.html", "gatilhos", status, choices=choices, **ctx)


@router.get("/automacoes/gatilhos/novo")
async def new_form(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    a = None
    if st.db_ok():
        choices = await views.catalog(st.pool)
        wanted = req.query_params.get("processo", "")
        a = next((x for x in choices if x.key == wanted), choices[0] if choices else None)
    rule = {"tipo": "intervalo", "intervalo": "15", "horario": "08:00"}
    return await form_page(st, req, a=a, values=a.defaults if a else {}, rule=rule)


async def load(st: State, tid: str) -> Trigger | None:
    n = to_int(tid)
    return await triggers.get_trigger(st.pool, n) if n is not None and st.db_ok() else None


@router.get("/automacoes/gatilhos/{tid}/editar")
async def edit_form(tid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    g = await load(st, tid)
    a = await views.runnable(st.pool, g.process_id, admin=True) if g is not None else None
    if g is None or a is None:
        return st.render.not_found()
    rule = {"tipo": g.kind, "intervalo": str(g.every_minutes or 15), "horario": g.at_time or "08:00"}
    return await form_page(st, req, a=a, trigger=g, values=a.defaults | g.params, rule=rule)


async def save(st: State, req: Request, tid: int | None) -> Response:
    form = {k: str(v) for k, v in (await req.form()).items()}
    if not st.db_ok():
        return await form_page(st, req, 503, a=None, rule=form)
    a = await views.runnable(st.pool, to_int(form.get("processo", "")), admin=True)
    if a is None or a.process is None:
        return await form_page(
            st, req, 422, a=None, rule=form, rule_errors={"processo": t("Escolha um processo.")}
        )
    raw = {k[2:]: v for k, v in form.items() if k.startswith("p_")}
    values, errors = validate(a.fields, raw)
    rule, rule_errors = parse_schedule(form)
    if rule is None or errors:
        ctx = {"a": a, "values": raw, "errors": errors, "rule": form, "rule_errors": rule_errors}
        return await form_page(st, req, 422, **ctx)
    async with st.pool.acquire() as c, c.transaction():
        new_id = await triggers.save_trigger(c, tid, a.process.id, dict(values), rule, user_of(req), now())
        action = "Gatilho criado" if tid is None else "Gatilho alterado"
        await triggers.audit(c, user_of(req), action, new_id, a.name, describe(rule))
        await mark_committed(c, req)
    return see_other("/gatilhos")


@router.post("/automacoes/gatilhos")
async def create(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    return await save(st, req, None)


@router.post("/automacoes/gatilhos/{tid}")
async def update(tid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    g = await load(st, tid)
    return await save(st, req, g.id) if g is not None else st.render.not_found()


@router.post("/automacoes/gatilhos/{tid}/ativo")
async def toggle(tid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    g = await load(st, tid)
    if g is None:
        return st.render.not_found()
    enabled = (await req.form()).get("ativo") == "1"
    async with st.pool.acquire() as c, c.transaction():
        await triggers.set_enabled(c, g, enabled, now())
        action = "Gatilho ativado" if enabled else "Gatilho desativado"
        await triggers.audit(c, user_of(req), action, g.id, g.process_name)
        await mark_committed(c, req)
    return see_other("/gatilhos")


@router.post("/automacoes/gatilhos/{tid}/excluir")
async def delete(tid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    g = await load(st, tid)
    if g is None:
        return st.render.not_found()
    async with st.pool.acquire() as c, c.transaction():
        if await triggers.delete_trigger(c, g.id):
            await triggers.audit(c, user_of(req), "Gatilho excluído", g.id, g.process_name)
        await mark_committed(c, req)
    return see_other("/gatilhos")
