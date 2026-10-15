"""The "Assets" section over Postgres: list with panels, create, save (optimistic locking), delete."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.render import html

from ativos import importer, views
from ativos.model import EDIT_FIELDS, validate
from ativos.query import Filter
from ativos.routes.imports import import_form
from ativos.state import (
    DB_ERRORS,
    State,
    StateDep,
    audit_tx,
    base_of,
    db_notice,
    get_job,
    list_assets,
    load_asset,
    sent_version,
    transient,
    values_from,
)
from ativos.ui import notice, param, thousands, wrap
from ativos.views import conflict, not_found, retry_after

INSERT_SQL = (
    "INSERT INTO assets (tag, name, category, location, status, responsible, department, "
    "acquisition_date, acquisition_value_cents, notes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
    "RETURNING id"
)
UPDATE_SQL = (
    "UPDATE assets SET name=$3, category=$4, location=$5, status=$6, responsible=$7, department=$8, "
    "acquisition_date=$9, acquisition_value_cents=$10, notes=$11, version = version + 1, "
    "updated_at = now() WHERE id = $1 AND version = $2 RETURNING version"
)
UPDATE_KEYS = tuple(k for k in EDIT_FIELDS if k != "tag")

router = APIRouter()


def _write_failed(
    e: BaseException, wrap_msg: Callable[[str], str], title: str, nothing: str = "Nada foi confirmado"
) -> Response:
    """A failed write: transient → 503 + ``Retry-After`` (nothing was committed); otherwise 500."""
    if transient(e):
        msg = notice("error", "Banco indisponível", f"{nothing}. Tente novamente em instantes.")
        return retry_after(html(wrap_msg(msg), 503))
    return html(wrap_msg(notice("error", title, importer.db_error_text(e))), 500)


async def _panel(st: State, q: Mapping[str, str], f: Filter, base: str) -> str:
    """The panel above the list (``?edit=``, ``?new=1``, ``?import=1``, ``?job=``)."""
    edit = param(q, "edit")
    if re.fullmatch(r"-?\d+", edit):
        row = await load_asset(st, int(edit))
        if row:
            return views.editor(row, base, "", f.qs())
        return notice("warn", "Ativo não encontrado", "Ele pode ter sido excluído.")
    if param(q, "new") == "1":
        return views.creator({}, base)
    if param(q, "import") == "1":
        return import_form(base)
    if param(q, "job"):
        j = await get_job(st, param(q, "job"), importer.KIND)
        if j is not None:
            return views.job_panel(importer.job_view(j), base)
    return ""


async def _list_body(st: State, f: Filter, panel: str, base: str) -> str:
    try:
        total, rows = await list_assets(st, f)
    except DB_ERRORS as e:
        return views.page_header("Inventário", views.header_actions(f, base)) + notice(
            "error", "Erro ao consultar os ativos", str(e)
        )
    return (
        views.page_header(
            f"{thousands(total)} ativos encontrados. Busca, filtros e ordenação no servidor.",
            views.header_actions(f, base),
        )
        + panel
        + '<div id="export-panel"></div>'
        + views.table(f, total, rows, base)
    )


@router.get("/ativos/assets")
async def assets_page(req: Request, st: StateDep) -> Response:
    n = db_notice(st)
    if n is not None:
        # No DB: the static mockup fragment, with the notice at the top.
        t = st.fragment("assets", req) or ""
        return html(t.replace('<div class="app-page-header">', f'{n}<div class="app-page-header">', 1))
    q = dict(req.query_params)
    f = Filter.from_query(q)
    try:
        panel = await _panel(st, q, f, base_of(req))
    except DB_ERRORS as e:
        panel = notice("error", "Erro ao carregar o ativo", str(e))
    body = await _list_body(st, f, panel, base_of(req))
    tpl = st.fragment("assets", req) or ""
    return html(wrap(tpl, body))


@router.post("/ativos/assets")
async def create(req: Request, st: StateDep) -> Response:
    form, base = values_from(await req.form()), base_of(req)
    if (n := db_notice(st)) is not None:
        return html(views.creator(form, base, n), 503)
    vals, errs = validate(form, EDIT_FIELDS)
    if errs:
        return html(views.creator(form, base, notice("error", "Verifique os campos", errs)), 422)
    tag = vals["tag"]
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            aid = await c.fetchval(INSERT_SQL, *[vals[k] for k in EDIT_FIELDS])
            await audit_tx(c, req, "Ativo cadastrado", tag, vals["name"] or "")
    except asyncpg.UniqueViolationError:
        msg = notice("error", "Patrimônio já cadastrado", "Use outro número de patrimônio.")
        return html(views.creator(form, base, msg), 409)
    except DB_ERRORS as e:
        return _write_failed(e, partial(views.creator, form, base), "Erro ao cadastrar")
    row = await load_asset(st, aid)
    ok = notice("ok", "Ativo cadastrado", f"Patrimônio {tag} criado.")
    return html(views.editor(row, base, ok) if row else notice("ok", "Ativo cadastrado", tag), 201)


async def _save(st: State, req: Request, aid: int, sent: int, vals: dict[str, Any], tag: str) -> Any:
    """The new version, or ``None`` when ``sent`` is outdated (or the asset is gone)."""
    async with st.sdk.pool.acquire() as c, c.transaction():
        version = await c.fetchval(UPDATE_SQL, aid, sent, *[vals[k] for k in UPDATE_KEYS])
        if version is not None:
            await audit_tx(c, req, "Ativo atualizado", tag, f"versão {sent} → {version}")
    return version


async def _stale(st: State, aid: int, sent: int, base: str) -> Response:
    """Outdated version (or the asset is gone): 409 with the message."""
    latest = await load_asset(st, aid)
    if latest is None:
        msg = notice("error", "Este ativo foi excluído por outra pessoa", "Suas alterações não foram salvas.")
        return html(msg, 409)
    return html(views.editor(latest, base, conflict(latest["version"], sent)), 409)


# Routes with a parameter go last: `/itens/import` and `/itens/export.*` take precedence
# (``app.create_app`` includes the import and export routers first).
@router.api_route("/ativos/itens/{aid}", methods=["POST", "GET"])
async def update(req: Request, aid: str, st: StateDep) -> Response:
    base = base_of(req)
    if req.method == "GET" or not aid.isdigit():
        return not_found(base)
    aid_n = int(aid)
    if (n := db_notice(st)) is not None:
        return html(n, 503)
    form = values_from(await req.form())
    sent = sent_version(form)
    try:
        current = await load_asset(st, aid_n)
    except DB_ERRORS as e:
        return html(notice("error", "Banco indisponível", str(e)), 503)
    if current is None:
        msg = notice("warn", "Ativo não encontrado", "Ele pode ter sido excluído por outra pessoa.")
        return html(msg, 404)
    vals, errs = validate(form, UPDATE_KEYS)
    if errs:
        return html(views.editor(current, base, notice("error", "Verifique os campos", errs)), 422)
    try:
        version = await _save(st, req, aid_n, sent, vals, views.asset_values(current)["tag"])
    except DB_ERRORS as e:
        return _write_failed(e, partial(views.editor, current, base), "Erro ao salvar")
    if version is None:
        return await _stale(st, aid_n, sent, base)
    row = await load_asset(st, aid_n) or current
    ok = notice("ok", "Alterações salvas", f"Versão {row['version']} gravada.")
    return html(views.editor(row, base, ok))


def _deleted(row: Any, base: str) -> Response:
    return html(
        '<section class="content-panel hub-panel" id="asset-editor">'
        + notice("ok", "Ativo excluído", f"{row['tag']} · {row['name']}")
        + f'<div class="modal-actions"><a class="primary-button" href="{base}/assets">Voltar à lista</a></div></section>'
    )


@router.api_route("/ativos/itens/{aid}/delete", methods=["POST", "GET"])
async def delete(req: Request, aid: str, st: StateDep) -> Response:
    base = base_of(req)
    if req.method == "GET" or not aid.isdigit():
        return not_found(base)
    aid_n = int(aid)
    if (n := db_notice(st)) is not None:
        return html(n, 503)
    sent = sent_version(values_from(await req.form()))
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            row = await c.fetchrow(
                "DELETE FROM assets WHERE id = $1 AND version = $2 RETURNING tag, name", aid_n, sent
            )
            if row is not None:
                await audit_tx(c, req, "Ativo excluído", row["tag"], row["name"])
    except DB_ERRORS as e:
        return _write_failed(e, str, "Erro ao excluir", "Nada foi excluído")
    if row is not None:
        return _deleted(row, base)
    cur = await load_asset(st, aid_n)
    if cur is not None:
        return html(views.editor(cur, base, conflict(cur["version"], sent)), 409)
    return html(notice("warn", "Ativo não encontrado", "Ele já foi excluído."), 404)
