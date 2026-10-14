"""Dynamic HTML of the "Assets" section: list, panels
for editing/creating, import and progress. Texts in pt-BR; the user's language is applied later
(:class:`ativos.i18n.I18nMiddleware`). No JavaScript: only ``data-hub-*`` (hub-kit.js)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ativos.model import (
    CATEGORIES,
    COL,
    COLS,
    EDIT_FIELDS,
    LOCATIONS,
    MAX_PAGE,
    PAGE_SIZE,
    SORTS,
    STATUSES,
    display,
    status_class,
)
from ativos.query import Filter
from ativos.ui import esc, money_cents, notice, thousands

FRAG = "/_frag"
BASE = "/apps/ativos"


def options(opts: tuple[str, ...], current: str, all_label: str | None = None) -> str:
    out: list[str] = [f'<option value="">{esc(all_label)}</option>'] if all_label is not None else []
    for o in opts:
        out.append(f'<option value="{esc(o)}"{" selected" if o == current else ""}>{esc(o)}</option>')
    return "".join(out)


def header_actions(f: Filter) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{esc(v)}">'
        for k, v in (
            ("q", f.q),
            ("categoria", f.categoria),
            ("localizacao", f.localizacao),
            ("status", f.status),
            ("sort", f.sort),
            ("dir", f.dir),
        )
        if v
    )
    return (
        f'<div class="hub-actions"><a class="secondary-button" href="{BASE}/assets{esc(f.qs(**{"import": "1"}))}">Importar Excel</a>'
        f'<a class="secondary-button" download href="{FRAG}/ativos/itens/export.csv{esc(f.qs(page=""))}">Exportar CSV</a>'
        f'<form class="hub-inline-form" method="post" action="{BASE}/itens/export.xlsx" data-hub-target="#export-panel">{hidden}'
        '<button type="submit" class="secondary-button">Exportar Excel</button></form>'
        f'<a class="primary-button" href="{BASE}/assets{esc(f.qs(new="1"))}">Novo ativo</a></div>'
    )


def page_header(subtitle: str, actions: str) -> str:
    return (
        '<div class="app-page-header"><div><div class="eyebrow" style="color:var(--app-ink,#644ce1)">GESTÃO DE ATIVOS</div>'
        f'<h1 class="page-title">Inventário de ativos</h1><p class="page-subtitle">{esc(subtitle)}</p></div>{actions}</div>'
    )


def table(f: Filter, total: int, rows: list[Any]) -> str:
    out = [
        f'<div class="content-panel"><form class="table-toolbar hub-filters" method="get" action="{BASE}/assets">'
        f'<input class="table-search" name="q" value="{esc(f.q)}" placeholder="Buscar por nome ou patrimônio..." aria-label="Buscar ativos">'
        f'<select name="categoria" aria-label="Categoria">{options(CATEGORIES, f.categoria, "Todas as categorias")}</select>'
        f'<select name="localizacao" aria-label="Localização">{options(LOCATIONS, f.localizacao, "Todas as localizações")}</select>'
        f'<select name="status" aria-label="Status">{options(STATUSES, f.status, "Todos os status")}</select>'
        f'<input type="hidden" name="sort" value="{esc(f.sort)}"><input type="hidden" name="dir" value="{esc(f.dir)}">'
        f'<button class="filter-button" type="submit">Filtrar</button><a class="hub-link" href="{BASE}/assets">Limpar</a></form>'
        '<div class="hub-table-scroll"><table class="data-table"><thead><tr>'
    ]
    for key, _col, label in SORTS:
        active = f.sort == key
        next_dir = "desc" if active and f.dir == "asc" else "asc"
        arrow = (" ↑" if f.dir == "asc" else " ↓") if active else ""
        out.append(
            f'<th><a class="hub-sort{" active" if active else ""}" href="{BASE}/assets'
            f'{esc(f.qs(sort=key, dir=next_dir, page=""))}">{esc(label)}{arrow}</a></th>'
        )
    out.append("<th></th></tr></thead><tbody>")
    if not rows:
        out.append(
            '<tr><td colspan="8" class="empty-state">Nenhum ativo encontrado com esses filtros.</td></tr>'
        )
    for r in rows:
        val = money_cents(r["acquisition_value_cents"]) if r["acquisition_value_cents"] is not None else ""
        acq = r["acquisition_date"].strftime("%d/%m/%Y") if r["acquisition_date"] else ""
        out.append(
            f'<tr><td><span class="table-primary">{esc(r["name"])}</span></td><td>{esc(r["tag"])}</td>'
            f"<td>{esc(r['category'])}</td><td>{esc(r['location'])}</td>"
            f'<td><span class="status-pill {status_class(r["status"])}">{esc(r["status"])}</span></td>'
            f'<td>{acq}</td><td>{val}</td><td><a class="hub-row-link" href="{BASE}/assets'
            f'{esc(f.qs(edit=str(r["id"])))}">Editar</a></td></tr>'
        )
    out.append("</tbody></table></div>")
    first = 0 if total == 0 else (f.page - 1) * PAGE_SIZE + 1
    last = max((f.page - 1) * PAGE_SIZE + len(rows), 0)
    pages = max(1, min((total + PAGE_SIZE - 1) // PAGE_SIZE, MAX_PAGE))

    def link(p: int | None, label: str) -> str:
        if p is None:
            return f'<span class="secondary-button hub-disabled">{label}</span>'
        return f'<a class="secondary-button" href="{BASE}/assets{esc(f.qs(page=str(p)))}">{label}</a>'

    out.append(
        f'<div class="table-footer"><span>Mostrando {thousands(first)} a {thousands(last)} de {thousands(total)} ativos · '
        f'página {thousands(f.page)} de {thousands(pages)}</span><div class="hub-pager">'
        f"{link(f.page - 1 if f.page > 1 else None, '‹ Anterior')}{link(f.page + 1 if f.page < pages else None, 'Próxima ›')}"
        "</div></div></div>"
    )
    return "".join(out)


def field_html(key: str, value: str, readonly: bool) -> str:
    c = COL[key]
    label = c.header + (" *" if c.required else "")
    if c.kind == "enum":
        inp = f'<select name="{key}" id="f-{key}">{options(c.options, value, None if c.required else "")}</select>'
    elif c.kind == "text" and c.max_len > 200:
        inp = f'<textarea name="{key}" id="f-{key}" maxlength="{c.max_len}">{esc(value)}</textarea>'
    else:
        inp = (
            f'<input name="{key}" id="f-{key}" value="{esc(value)}"{" readonly" if readonly else ""}'
            f"{' required' if c.required else ''}>"
        )
    return f'<div class="form-field"><label for="f-{key}">{esc(label)}</label>{inp}</div>'


def asset_values(row: Any) -> dict[str, str]:
    """Values of a record as the form shows them (dates dd/mm/yyyy, values 1234,56)."""
    return {c.key: display(c, row[c.key]) for c in COLS}


def editor(row: Any, msg: str = "", close_qs: str = "") -> str:
    v = asset_values(row)
    fields = "".join(field_html(k, v[k], k == "tag") for k in EDIT_FIELDS)
    ver, rid = row["version"], row["id"]
    return (
        '<section class="content-panel hub-panel" id="asset-editor"><div class="hub-panel-head"><div>'
        f'<span class="eyebrow">EDITAR ATIVO</span><h2>{esc(v["name"])}</h2><p class="hub-muted">Patrimônio {esc(v["tag"])} · versão {ver}</p></div>'
        f'<a class="secondary-button" href="{BASE}/assets{esc(close_qs)}">Fechar</a></div>{msg}'
        f'<form class="hub-form" method="post" action="{BASE}/itens/{rid}" data-hub-target="#asset-editor">'
        f'<input type="hidden" name="version" value="{ver}"><div class="hub-form-grid">{fields}</div>'
        '<div class="modal-actions"><button type="submit" class="primary-button">Salvar alterações</button></div></form>'
        f'<form class="hub-form hub-inline" method="post" action="{BASE}/itens/{rid}/delete" data-hub-target="#asset-editor">'
        f'<input type="hidden" name="version" value="{ver}"><button type="submit" class="secondary-button hub-danger">Excluir ativo</button></form></section>'
    )


def creator(values: dict[str, str], msg: str = "") -> str:
    fields = "".join(field_html(k, values.get(k, ""), False) for k in EDIT_FIELDS)
    return (
        '<section class="content-panel hub-panel" id="asset-editor"><div class="hub-panel-head"><div>'
        '<span class="eyebrow">NOVO ATIVO</span><h2>Cadastrar ativo</h2><p class="hub-muted">Campos com * são obrigatórios.</p></div>'
        f'<a class="secondary-button" href="{BASE}/assets">Fechar</a></div>{msg}'
        f'<form class="hub-form" method="post" action="{BASE}/assets" data-hub-target="#asset-editor">'
        f'<div class="hub-form-grid">{fields}</div><div class="modal-actions">'
        '<button type="submit" class="primary-button">Cadastrar ativo</button></div></form></section>'
    )


def import_form(msg: str, max_mb: int, max_rows: int) -> str:
    return (
        '<section class="content-panel hub-panel" id="import-panel"><div class="hub-panel-head"><div>'
        f'<span class="eyebrow">IMPORTAÇÃO</span><h2>Importar planilha Excel</h2><p class="hub-muted">Arquivo .xlsx de até {max_mb} MB '
        f"(até {thousands(max_rows)} linhas), com os cabeçalhos do modelo. As linhas válidas são gravadas; as demais vão para um relatório de erros.</p></div>"
        f'<a class="secondary-button" href="{BASE}/assets">Fechar</a></div>{msg}'
        f'<form class="hub-form" method="post" action="{BASE}/itens/import" enctype="multipart/form-data" data-hub-target="#import-panel">'
        '<div class="form-field"><label for="f-file">Planilha (.xlsx)</label><input id="f-file" type="file" name="file" '
        'accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></div>'
        '<div class="modal-actions"><span class="hub-muted" data-hub-progress></span>'
        f'<a class="secondary-button" download href="{FRAG}/ativos/itens/modelo.xlsx">Baixar modelo</a>'
        '<button type="submit" class="primary-button">Importar</button></div></form></section>'
    )


def job_panel(j: dict[str, Any]) -> str:
    """Import panel (``importer.job_view``)."""
    pct = min(j["read"] * 100 // j["total"], 100) if j["total"] else 0
    stats = (
        f"{thousands(j['read'])} de {thousands(j['total'])} linhas lidas · {thousands(j['inserted'])} gravadas · "
        f"{thousands(j['rejected'])} rejeitadas"
    )
    jid = esc(j["id"])
    report = (
        f'<a class="secondary-button" download href="{FRAG}/ativos/itens/import/{jid}/erros.csv">'
        f"Baixar relatório de erros ({thousands(j['error_lines'])})</a>"
    )
    retry = (
        f" · retomada {j['attempts']} de {j['max_attempts']} a partir da linha {thousands(j['checkpoint'])}"
        if j["attempts"] > 1
        else ""
    )
    file = esc(j["file"])
    state = j["state"]
    if state in ("queued", "running"):
        queued = state == "queued"
        hint = (
            "Aguardando uma vaga: o app processa poucas operações pesadas ao mesmo tempo."
            if queued
            else "Você pode continuar usando o app; a importação roda em segundo plano e sobrevive a reinícios."
        )
        return (
            f'<section class="content-panel hub-panel" id="import-panel" data-hub-poll="{BASE}/itens/import/{jid}" data-hub-interval="1000">'
            f'<div class="hub-panel-head"><div><span class="eyebrow">{"IMPORTAÇÃO NA FILA" if queued else "IMPORTAÇÃO EM ANDAMENTO"}</span>'
            f'<h2>{file}</h2><p class="hub-muted">{stats} · {j["elapsed"]:.0f} s{retry}</p></div><strong class="hub-pct">{pct}%</strong></div>'
            f'<div class="hub-progress" role="progressbar" aria-valuenow="{pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:{pct}%"></i></div>'
            f'<p class="hub-muted">{hint}</p></section>'
        )
    if state == "done":
        note = (
            notice("ok", "Tudo certo", "Todas as linhas foram importadas.")
            if j["rejected"] == 0
            else notice(
                "warn",
                "Importação concluída com rejeições",
                "Baixe o relatório para ver a linha, a coluna e o motivo de cada erro.",
            )
        )
        return (
            '<section class="content-panel hub-panel" id="import-panel"><div class="hub-panel-head"><div>'
            f'<span class="eyebrow">IMPORTAÇÃO CONCLUÍDA</span><h2>{file}</h2><p class="hub-muted">{stats} · {j["elapsed"]:.1f} s{retry}</p></div>'
            f'<a class="secondary-button" href="{BASE}/assets">Fechar</a></div><div class="hub-progress done"><i style="width:100%"></i></div>{note}'
            f'<div class="modal-actions">{report if j["rejected"] > 0 else ""}<a class="primary-button" href="{BASE}/assets">Ver ativos</a></div></section>'
        )
    if state == "cancelled":
        return (
            '<section class="content-panel hub-panel" id="import-panel"><div class="hub-panel-head"><div>'
            f'<span class="eyebrow">IMPORTAÇÃO CANCELADA</span><h2>{file}</h2><p class="hub-muted">{stats}{retry}</p></div>'
            f'<a class="secondary-button" href="{BASE}/assets">Fechar</a></div>'
            + notice(
                "warn",
                "Importação cancelada",
                "As linhas já confirmadas foram mantidas; o restante não foi importado.",
            )
            + "</section>"
        )
    return (
        '<section class="content-panel hub-panel" id="import-panel"><div class="hub-panel-head"><div>'
        f'<span class="eyebrow">IMPORTAÇÃO INTERROMPIDA</span><h2>{file}</h2><p class="hub-muted">{stats}{retry}</p></div>'
        f'<a class="secondary-button" href="{BASE}/assets?import=1">Tentar novamente</a></div>'
        + notice("error", "Não foi possível importar a planilha", j["error"])
        + (report if j["error_lines"] > 0 else "")
        + "</section>"
    )


_ = dt
