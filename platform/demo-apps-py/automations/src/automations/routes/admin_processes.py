"""Processes (admins): list, new/edit - package + version (the version select depends on the
package), timeout, enabled and default parameters (JSON, checked against the package fields)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.locale import t
from pydantic import ValidationError

from automations import packages, processes
from automations.access import is_admin
from automations.model import Process
from automations.processes import ProcessConflict, ProcessIn
from automations.state import DB_ERRORS, State, StateDep, mark_committed, see_other, to_int, user_of

router = APIRouter()
# Form field (pt-BR, like the UI) ← model field.
FORM = {"name": "nome", "package_name": "pacote", "package_version": "versao", "timeout_s": "tempo",
        "params": "parametros", "enabled": "ativo"}  # fmt: skip


@router.get("/automations/processes")
async def listing(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    items: list[Process] = []
    if st.db_ok():
        try:
            items = await processes.list_processes(st.pool, only_enabled=False)
        except DB_ERRORS:
            items = []
    return st.page(req, "processos.html", "processes", items=items)


async def form_page(st: State, req: Request, status: int = 200, **ctx: Any) -> Response:
    """``ctx``: ``process`` (edit), ``form`` (values by form field), ``errors`` (by form field)."""
    ctx.setdefault("process", None)
    ctx.setdefault("errors", {})
    versions = await packages.versions(st.pool) if st.db_ok() else {}
    ctx["form"] = preselect(ctx.get("form") or {}, versions)
    options = {name: [[v, v] for v in vs] for name, vs in versions.items()}
    return st.page(req, "processo.html", "processes", status, versions=versions,
                   options_json=json.dumps(options, ensure_ascii=False), **ctx)  # fmt: skip


def preselect(form: dict[str, str], versions: dict[str, list[str]]) -> dict[str, str]:
    """A new form starts on the first package and its newest version (the version select depends on it)."""
    if form.get("pacote", "") != "" or not versions:
        return form
    name = next(iter(versions))
    return form | {"pacote": name, "versao": versions[name][0]}


def form_of(p: Process | None) -> dict[str, str]:
    if p is None:
        return {"tempo": "300", "ativo": "1", "parametros": "{}"}
    return {"nome": p.name, "pacote": p.package_name, "versao": p.package_version, "tempo": str(p.timeout_s),
            "ativo": "1" if p.enabled else "", "parametros": json.dumps(p.params, ensure_ascii=False, indent=2)}  # fmt: skip


@router.get("/automations/processes/new")
async def new_form(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    return await form_page(st, req, form=form_of(None))


async def load(st: State, pid: str) -> Process | None:
    n = to_int(pid)
    return await processes.get_process(st.pool, n) if n is not None and st.db_ok() else None


@router.get("/automations/processes/{pid}/edit")
async def edit_form(pid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    p = await load(st, pid)
    if p is None:
        return st.render.not_found()
    return await form_page(st, req, process=p, form=form_of(p))


def parse(form: dict[str, str]) -> tuple[ProcessIn | None, dict[str, str]]:
    try:
        params = json.loads(form.get("parametros") or "{}")
    except ValueError:
        return None, {"parametros": t("JSON inválido.")}
    data = {"name": form.get("nome", "").strip(), "package_name": form.get("pacote", ""),
            "package_version": form.get("versao", ""), "timeout_s": form.get("tempo") or 300,
            "params": params, "enabled": form.get("ativo") == "1"}  # fmt: skip
    try:
        return ProcessIn.model_validate(data), {}
    except ValidationError as e:
        return None, {FORM.get(str(err["loc"][0]), "nome"): t("Valor inválido.") for err in e.errors()}


async def checked(st: State, form: dict[str, str]) -> tuple[ProcessIn | None, dict[str, str]]:
    """The process input, its default params coerced against the selected package version."""
    spec, errors = parse(form)
    if spec is None:
        return None, errors
    pkg = await packages.get_package(st.pool, spec.package_name, spec.package_version)
    if pkg is None:
        return None, {"versao": t("Esta versão do pacote não existe.")}
    values, perr = processes.check_defaults(pkg.specs, spec.params)
    if perr:
        return None, {"parametros": "; ".join(f"{k}: {v}" for k, v in perr.items())}
    return spec.model_copy(update={"params": values}), {}


async def save(st: State, req: Request, old: Process | None) -> Response:
    form = {k: str(v) for k, v in (await req.form()).items()}
    if not st.db_ok():
        return await form_page(st, req, 503, process=old, form=form)
    spec, errors = await checked(st, form)
    if spec is None:
        return await form_page(st, req, 422, process=old, form=form, errors=errors)
    try:
        async with st.pool.acquire() as c, c.transaction():
            await processes.save_process(c, old, spec, user_of(req))
            await mark_committed(c, req)
    except ProcessConflict as e:
        return await form_page(st, req, 409, process=old, form=form, errors={"nome": str(e)})
    return see_other("/processes")


@router.post("/automations/processes")
async def create(req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    return await save(st, req, None)


@router.post("/automations/processes/{pid}")
async def update(pid: str, req: Request, st: StateDep) -> Response:
    if not is_admin(req):
        return st.forbidden(req)
    p = await load(st, pid)
    if p is None:
        return st.render.not_found()
    return await save(st, req, p)
