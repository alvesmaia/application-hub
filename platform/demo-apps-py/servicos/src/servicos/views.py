"""Markup of the Service Center's dynamic screens."""

from __future__ import annotations

from typing import Any

from fastapi.responses import Response
from hub_sdk.render import html

from servicos.model import (
    CATEGORIES,
    PAGE,
    PRIORITIES,
    STATUSES,
    priority_class,
    sla_text,
    status_class,
)
from servicos.report import GRANULARITIES, Params
from servicos.ui import esc, query, thousands

REPORTS_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3v18h18"></path>'
    '<path d="m7 14 4-4 4 3 6-7"></path></svg>'
)


def inject_nav(body: str, section: str) -> str:
    """\"Reports\" item in the Service Center sidebar (on all pages)."""
    active = "active" if section == "reports" else ""
    item = f'<a class="nav-item {active}" href="/apps/servicos/reports">{REPORTS_ICON}<span>Relatórios</span></a>'
    out = body
    if section == "reports":
        out = out.replace('class="nav-item active"', 'class="nav-item "')
    for marker in (
        '<a class="nav-item " href="/apps/servicos/settings">',
        '<a class="nav-item active" href="/apps/servicos/settings">',
    ):
        pos = out.find(marker)
        if pos >= 0:
            return out[:pos] + item + out[pos:]
    return out


def options(opts: tuple[str, ...], current: str, all_label: str | None = None) -> str:
    out = f'<option value="">{esc(all_label)}</option>' if all_label is not None else ""
    for o in opts:
        out += f'<option value="{esc(o)}"{" selected" if o == current else ""}>{esc(o)}</option>'
    return out


def row_html(t: Any, msg: str = "") -> str:
    statuses = "".join(
        f"<option{' selected' if s == t['status'] else ''}>{esc(s)}</option>" for s in STATUSES
    )
    sla = sla_text(t["status"], t["sla_due"])
    sla_cell = '<span class="app3-sla-late">Vencido</span>' if sla == "Vencido" else esc(sla)
    i = t["id"]
    return (
        f'<tr id="ticket-{i}"><td><strong>{esc(t["title"])}</strong>{msg}</td><td><code>{esc(t["protocol"])}</code></td>'
        f"<td>{esc(t['requester'])}</td><td>{esc(t['category'])}</td>"
        f'<td><span class="app3-priority {priority_class(t["priority"])}">{esc(t["priority"])}</span></td>'
        f'<td><span class="app3-request-status {status_class(t["status"])}">{esc(t["status"])}</span></td>'
        f'<td class="app3-sla-time">{sla_cell}</td><td class="row-actions">'
        '<details class="hub-row-menu"><summary class="row-action" aria-label="Alterar status" title="Alterar status">⋯</summary>'
        '<div class="hub-row-menu-list app3-status-menu">'
        f'<form class="hub-inline" method="post" action="/apps/servicos/requests/{i}" data-hub-target="#ticket-{i}">'
        f'<input type="hidden" name="version" value="{t["version"]}">'
        f'<label>Alterar status<select name="status">{statuses}</select></label>'
        '<button type="submit" class="secondary-button">Salvar</button></form></div></details></td></tr>'
    )


def new_form(msg: str = "") -> str:
    return (
        '<section class="app3-panel hub-panel" id="ticket-new"><div class="hub-panel-head"><div>'
        '<span class="app3-overline">NOVA SOLICITAÇÃO</span><h2>Abrir chamado</h2></div>'
        '<a class="secondary-button" href="/apps/servicos/requests">Fechar</a></div>'
        f'{msg}<form class="hub-form" method="post" action="/apps/servicos/requests" data-hub-target="#ticket-new">'
        '<div class="hub-form-grid"><div class="form-field"><label for="t-title">Título *</label>'
        '<input id="t-title" name="title" required maxlength="160"></div>'
        '<div class="form-field"><label for="t-cat">Categoria *</label>'
        f'<select id="t-cat" name="category">{options(CATEGORIES, "")}</select></div>'
        '<div class="form-field"><label for="t-pri">Prioridade *</label>'
        f'<select id="t-pri" name="priority">{options(PRIORITIES, "Normal")}</select></div>'
        '<div class="form-field hub-wide"><label for="t-desc">Descrição</label>'
        '<textarea id="t-desc" name="description" maxlength="4000"></textarea></div>'
        '<div class="form-field hub-wide"><label class="hub-check-label" for="t-approval">'
        '<input id="t-approval" type="checkbox" name="approval" value="1"> '
        "Precisa de aprovação (vai para a Inbox dos aprovadores)</label></div></div>"
        '<div class="modal-actions"><button type="submit" class="primary-button">Abrir chamado</button></div>'
        "</form></section>"
    )


def heading_and_stats(counts: dict[str, int]) -> str:
    return (
        '<div class="app3-page-heading"><div><span class="app3-overline">CENTRAL DE SERVIÇOS</span>'
        "<h1>Solicitações</h1><p>Acompanhe e atualize os chamados da equipe.</p></div>"
        '<div class="hub-actions"><a class="app3-outline" href="/apps/servicos/reports">Relatório gerencial</a>'
        '<a class="app3-primary" href="/apps/servicos/requests?new=1">Nova solicitação</a></div></div>'
        '<div class="app3-request-stats">'
        f"<div><strong>{thousands(counts.get('Aberto', 0))}</strong><span>Abertos</span></div>"
        f"<div><strong>{thousands(counts.get('Em atendimento', 0))}</strong><span>Em atendimento</span></div>"
        f"<div><strong>{thousands(counts.get('Aguardando', 0))}</strong><span>Aguardando</span></div></div>"
    )


def filters(proto: str, status: str, cat: str, pri: str) -> str:
    return (
        '<form class="app3-request-toolbar hub-filters" method="get" action="/apps/servicos/requests">'
        f'<input class="table-search" name="protocolo" value="{esc(proto)}" placeholder="Protocolo (SD-2026-...)" aria-label="Protocolo">'
        f'<select name="status" aria-label="Status">{options(STATUSES, status, "Todos os status")}</select>'
        f'<select name="categoria" aria-label="Categoria">{options(CATEGORIES, cat, "Todas as categorias")}</select>'
        f'<select name="prioridade" aria-label="Prioridade">{options(PRIORITIES, pri, "Todas as prioridades")}</select>'
        '<button class="filter-button" type="submit">Filtrar</button>'
        '<a class="hub-link" href="/apps/servicos/requests">Limpar</a></form>'
    )


def table(tickets: list[Any], base: str, before: bool) -> str:
    out = (
        '<section class="app3-panel app3-request-list"><div class="app3-table-scroll"><table class="app3-table">'
        "<thead><tr><th>SOLICITAÇÃO</th><th>PROTOCOLO</th><th>SOLICITANTE</th><th>CATEGORIA</th><th>PRIORIDADE</th>"
        '<th>STATUS</th><th>SLA</th><th><span class="visually-hidden">AÇÕES</span></th></tr></thead><tbody>'
    )
    if not tickets:
        out += '<tr><td colspan="8" class="empty-state">Nenhum chamado com esses filtros.</td></tr>'
    out += "".join(row_html(t) for t in tickets)
    out += "</tbody></table></div>"
    first = (
        f'<a class="secondary-button" href="/apps/servicos/requests{esc(base)}">« Mais recentes</a>'
        if before
        else ""
    )
    nxt = ""
    if tickets and len(tickets) == PAGE:
        sep = "?" if not base else "&"
        nxt = (
            f'<a class="secondary-button" href="/apps/servicos/requests{esc(base)}{sep}antes={tickets[-1]["id"]}">'
            "Mais antigos ›</a>"
        )
    since = f"{tickets[-1]['opened_at']:%d/%m/%Y %H:%M}" if tickets else ""
    return (
        out
        + f'<footer class="table-footer"><span>{len(tickets)} chamados nesta página · abertos a partir de {since}</span>'
        f'<div class="hub-pager">{first}{nxt}</div></footer></section>'
    )


def reports_head(p: Params) -> str:
    gran = "".join(
        f'<option value="{k}"{" selected" if k == p.gran else ""}>{label}</option>'
        for k, label in GRANULARITIES
    )
    return (
        '<div class="app3-page-heading"><div><span class="app3-overline">GESTÃO</span><h1>Relatório gerencial</h1>'
        "<p>Volume, resolução e SLA por categoria e período, calculados sobre todos os chamados.</p></div></div>"
        '<form class="app3-request-toolbar hub-filters" method="get" action="/apps/servicos/reports">'
        f'<label class="hub-label">De <input type="date" name="de" value="{p.from_:%Y-%m-%d}"></label>'
        f'<label class="hub-label">Até <input type="date" name="ate" value="{p.to:%Y-%m-%d}"></label>'
        f'<select name="agrupar" aria-label="Agrupar por">{gran}</select>'
        f'<select name="categoria" aria-label="Categoria">{options(CATEGORIES, p.category, "Todas as categorias")}</select>'
        '<button class="filter-button" type="submit">Gerar relatório</button></form>'
    )


def base_query(status: str, cat: str, pri: str, proto: str) -> str:
    return query([("status", status), ("categoria", cat), ("prioridade", pri), ("protocolo", proto)])


def not_found() -> Response:
    return html(
        '<section class="empty-state"><h1>Página não encontrada</h1><p>Esta seção não existe. '
        '<a href="/apps/servicos">Voltar ao início do app</a>.</p></section>',
        404,
    )


def retry_after(resp: Response, secs: int = 2) -> Response:
    resp.headers["retry-after"] = str(secs)
    return resp
