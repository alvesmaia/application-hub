"""Management report (per-session cache, heavy-operation slot) and its CSV export."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.heavy import Permit
from hub_sdk.render import busy, download_headers, html

from servicos import report, views
from servicos.state import DBERR, State, StateDep, db_notice, session_key, user_of, with_cookie
from servicos.ui import notice, param, thousands, wrap

router = APIRouter()


async def _result(st: State, key: str, params: report.Params) -> str | None:
    """The rendered report (the session's cached one, or a fresh run); ``None`` when there is no
    heavy-operation slot for a fresh run."""
    cached = st.sessions.get_report(key, params.key())
    permit: Permit | None = None
    if cached is None:
        permit = st.sdk.heavy.try_acquire()
        if permit is None:
            return None
    try:
        result = cached or await report.run(st.sdk.pool, params)
    except DBERR as e:
        return notice("error", "Erro ao gerar o relatório", str(e))
    else:
        st.sessions.put_report(key, params.key(), result)
        return report.render(result, params, cached is not None)
    finally:
        if permit is not None:
            permit.release()


def _sessions_line(st: State) -> str:
    s = st.sessions.stats()
    return (
        '<p class="hub-muted hub-session-stats">'
        f"Cache de sessões: {thousands(s.sessions)} de {thousands(s.capacity)} usuários ativos · "
        f"~{thousands(s.approx_bytes // 1024)} KiB em memória · {st.sdk.heavy.in_use} operações pesadas "
        f"em andamento (limite {st.sdk.heavy.max}).</p>"
    )


@router.get("/servicos/reports")
async def reports(req: Request, st: StateDep) -> Response:
    key, new_sid = session_key(req)
    q = dict(req.query_params)
    params = report.Params.from_query(q)
    body = views.reports_head(params)
    if not param(q, "de"):
        body += notice(
            "info",
            "Escolha o período",
            "O relatório agrega todos os chamados do período (milhões de linhas); cada sessão guarda o último resultado em cache.",
        )
    elif (n := db_notice(st)) is not None:
        body += n
    else:
        part = await _result(st, key, params)
        if part is None:
            return with_cookie(busy("O relatório", "Relatório em fila…"), new_sid)
        body += part
    body += _sessions_line(st)
    tpl = st.page("requests", req, "reports") or ""
    return with_cookie(html(wrap(tpl, f'<div class="app3-page">{body}</div>')), new_sid)


@router.get("/servicos/reports/export.csv")
async def export_csv(req: Request, st: StateDep) -> Response:
    n = db_notice(st)
    if n is not None:
        return html(n, 503)
    permit = st.sdk.heavy.try_acquire()
    if permit is None:
        return busy("A exportação do relatório", "Exportação em fila…")
    params = report.Params.from_query(dict(req.query_params))
    try:
        with permit:
            result = await report.run(st.sdk.pool, params)
    except DBERR as e:
        return html(notice("error", "Não foi possível exportar", str(e)), 503)
    await st.sdk.emit(
        user_of(req), "Relatório de chamados exportado", "relatorio-chamados.csv", params.describe()
    )
    return Response(
        report.csv(result).encode("utf-8"),
        headers=download_headers("text/csv; charset=utf-8", "relatorio-chamados.csv"),
    )
