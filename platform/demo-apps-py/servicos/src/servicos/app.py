"""Central de Serviços — app FastAPI da plataforma (``HUB_DEMO_APP=servicos``; mesmas rotas, mesmo markup, mesma base de dados).

Chamados no Postgres (milhões de linhas, alto volume de escrita), relatório gerencial agregado
com exportação CSV e cache de sessão por usuário em memória (LRU limitado).

Rotas (todas sob ``/servicos``; o hub as expõe em ``/_frag/servicos/...``):

- ``GET /servicos`` · ``/servicos/{dashboard,approvals,knowledge,settings}`` — fragmentos do mockup
  (``fragments/``), todos com o item extra "Relatórios" na sidebar;
- ``GET /servicos/requests?status=&categoria=&prioridade=&protocolo=&antes=`` (paginação por chave,
  rápida em qualquer profundidade) e ``?new=1``; ``POST /servicos/requests`` (abrir) e
  ``POST /servicos/requests/{id}`` (mudar status, com ``version``: desatualizada → 409);
- ``GET /servicos/reports?de=&ate=&agrupar=&categoria=`` e ``…/reports/export.csv`` (operação pesada);
- ``GET /servicos/_skeleton`` · ``/servicos/assets/{demo.css,app.css}`` (sem JavaScript);
- ``/servicos/api/v1/*`` (OpenAPI em ``/servicos/api/openapi.json``; ferramentas MCP geradas dele) e
  ``/servicos/_hub/*`` (endpoints internos do hub-sdk, inclusive ``approvals`` — decisão da Inbox).

Toda ação que grava dados grava também o evento de auditoria no outbox e a marca de idempotência
**na mesma transação** (abrir com "Precisa de aprovação" publica o pedido na Inbox do hub).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
import unicodedata
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import Response
from hub_sdk import approvals as ap
from hub_sdk import e2e_hostile, outbox
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.heavy import Permit
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.locale import from_headers
from hub_sdk.render import busy, download_headers, html
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score

from servicos import report, views
from servicos.i18n import I18nMiddleware, personalize
from servicos.model import (
    CATEGORIES,
    INSERT_SQL,
    PAGE,
    PRIORITIES,
    REDATE_SQL,
    SCHEMA_SQL,
    SEED_CHUNK,
    SEED_SQL,
    STALE_SQL,
    STATUSES,
    TICKET_COLS,
    TICKETS_BASE,
    UPDATE_SQL,
    pick,
    sla_for,
)
from servicos.model import now as utcnow
from servicos.session import Sessions
from servicos.ui import notice, param, thousands, url_enc, wrap

log = logging.getLogger("servicos")

APP_ID = "servicos"
HERE = Path(__file__).resolve().parent
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]
CSS = "text/css; charset=utf-8"
COUNT_TTL_S = 15.0
NOUNS = (
    "chamado",
    "chamados",
    "solicitacao",
    "solicitacoes",
    "ticket",
    "tickets",
    "request",
    "requests",
    "solicitud",
)
DBERR = (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError)
INT64 = re.compile(r"[+-]?[0-9]+", re.ASCII)


def to_i64(s: str) -> int | None:
    v = int(s) if INT64.fullmatch(s) else None
    return v if v is not None and -(2**63) <= v < 2**63 else None


def shared_assets_dir() -> Path:
    """``demo.css`` compartilhado: ``SERVICOS_SHARED_ASSETS`` ou ``platform/demo-apps/assets``."""
    env = os.environ.get("SERVICOS_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def approver_roles() -> list[str]:
    raw = os.environ.get("HUB_SERVICOS_APPROVER_ROLES", "Administrador")
    return [r.strip() for r in raw.split(",") if r.strip()]


# ---- carga inicial -------------------------------------------------------------------------------
def seed_rows() -> int:
    """Linhas da carga: ``SERVICOS_SEED_ROWS`` ou 2 milhões × ``HUB_SEED_SCALE``."""
    if os.environ.get("SERVICOS_SEED_ROWS", ""):
        try:
            return int(os.environ["SERVICOS_SEED_ROWS"])
        except ValueError:
            return 0
    try:
        scale = float(os.environ.get("HUB_SEED_SCALE", "1"))
    except ValueError:
        scale = 1.0
    return round(TICKETS_BASE * (scale if scale >= 0 else 1.0))


async def seed_into(c: Any, rows: int) -> None:
    """Carga idempotente e retomável: lotes de 50 mil gerados no Postgres; lote presente é pulado."""
    t0 = time.monotonic()
    total = str(max(rows, 1))
    sql = SEED_SQL.replace("{TOTAL}", total)
    for start in range(1, rows + 1, SEED_CHUNK):
        end = min(start + SEED_CHUNK - 1, rows)
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM tickets WHERE id = $1)", end):
            await c.execute(sql, start, end)
    if await c.fetchval(STALE_SQL):
        await c.execute(REDATE_SQL.replace("{TOTAL}", total))
    await c.execute("ANALYZE tickets")
    log.warning("servicos: carga de %d chamados pronta (%.0f s)", rows, time.monotonic() - t0)


async def seed(sdk: HubSdk) -> None:
    rows = seed_rows()
    if rows <= 0 or sdk.pool is None:
        return
    async with sdk.pool.acquire() as c:
        await c.execute("SELECT pg_advisory_lock(424245)")
        try:
            await seed_into(c, rows)
        finally:
            await c.execute("SELECT pg_advisory_unlock(424245)")


async def run_seed() -> int:
    """``python -m servicos seed``: migra o esquema e faz a carga (2 milhões × ``HUB_SEED_SCALE``)."""
    from hub_sdk.db import app_env

    url = app_env(APP_ID, "HUB_DB_URL")
    if url is None:
        print("defina HUB_DB_URL (ou HUB_DB_URL_SERVICOS)", flush=True)
        return 2
    c = await asyncpg.connect(url)
    try:
        await c.execute(SCHEMA_SQL)
        await seed_into(c, seed_rows())
    finally:
        await c.close()
    return 0


# ---- busca federada ------------------------------------------------------------------------------
def fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """A busca é o nome da entidade ("chamados", "ticket")? Então os mais recentes respondem."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


def make_search(sdk: HubSdk) -> Any:
    """Busca federada (onda 9): protocolo exato (índice único) + título/protocolo nos 20 mil chamados
    mais recentes → ``/apps/servicos/requests?protocolo=<protocolo>``."""

    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        exact = q.q.upper()
        generic = generic_term(q.q)
        async with sdk.pool.acquire() as c:
            if generic:
                rows = await c.fetch(
                    "SELECT protocol, title, status, category FROM tickets ORDER BY id DESC LIMIT $1", q.limit
                )
            else:
                rows = await c.fetch(
                    "(SELECT protocol, title, status, category FROM tickets WHERE protocol = $1) UNION "
                    "(SELECT protocol, title, status, category FROM (SELECT protocol, title, status, category "
                    "FROM tickets ORDER BY id DESC LIMIT 20000) r WHERE title ILIKE $2 OR protocol ILIKE $2 LIMIT $3)",
                    exact,
                    like_pattern(q.q),
                    q.limit,
                )
        kind = {"en": "Ticket"}.get(q.locale, "Chamado")
        return [
            SearchHit(
                title=r["title"],
                subtitle=f"{r['protocol']} · {r['status']} · {r['category']}",
                url=f"/apps/servicos/requests?protocolo={url_enc(r['protocol'])}",
                kind=kind,
                score=1.0
                if r["protocol"] == exact
                else 0.5
                if generic
                else max(score(r["title"], q.q), score(r["protocol"], q.q), 0.3),
            )
            for r in rows
        ]

    return search


# ---- aprovações pela Inbox -----------------------------------------------------------------------
async def request_approval(
    c: Any, ticket_id: int, protocol: str, title: str, requester_login: str, requester_name: str
) -> None:
    """Pedido de aprovação de um chamado recém-aberto (na transação da abertura)."""
    await c.execute(
        "INSERT INTO ticket_approvals (ticket_id, requested_by, requested_by_name) VALUES ($1, $2, $3) "
        "ON CONFLICT DO NOTHING",
        ticket_id,
        requester_login,
        requester_name,
    )
    ev = outbox.approval_request(
        requester_login,
        f"sd-{ticket_id}",
        f"Aprovar chamado {protocol}",
        f"{requester_name} pediu aprovação: {title}",
        f"/apps/servicos/requests?protocolo={url_enc(protocol)}",
        to_roles=approver_roles(),
        level="warning",
    )
    await outbox.write(c, ev)


def make_approvals(sdk: HubSdk) -> Any:
    """Decisão vinda da Inbox do hub (``POST /servicos/_hub/approvals/sd-<id>``): aprovado → "Em
    atendimento"; recusado → "Fechado"; auditoria + notificação a quem pediu, na mesma transação."""

    async def decide(d: ap.ApprovalDecision) -> str:
        m = re.fullmatch(r"sd-([0-9]+)", d.approval_id)
        if m is None or (tid := to_i64(m.group(1))) is None:
            raise ap.NotFound
        if sdk.pool is None:
            raise RuntimeError("sem banco")
        async with sdk.pool.acquire() as c, c.transaction():
            row = await c.fetchrow(
                "SELECT a.status, a.requested_by, t.protocol, t.title FROM ticket_approvals a "
                "JOIN tickets t ON t.id = a.ticket_id WHERE a.ticket_id = $1 FOR UPDATE OF a",
                tid,
            )
            if row is None:
                raise ap.NotFound
            if row["status"] != "pending":
                raise ap.AlreadyDecided(row["status"])
            approver = d.approver.user
            if approver.lower() == row["requested_by"].lower():
                raise ap.Forbidden("quem pediu não aprova o próprio chamado")
            final = d.status
            ticket_status = "Em atendimento" if final == "approved" else "Fechado"
            await c.execute(
                "UPDATE ticket_approvals SET status = $2, decided_by = $3, decided_at = now(), comment = $4 "
                "WHERE ticket_id = $1",
                tid,
                final,
                approver,
                d.comment,
            )
            await c.execute(
                "UPDATE tickets SET status = $2::text, version = version + 1, updated_at = now(), "
                "resolved_at = CASE WHEN $2::text = 'Fechado' THEN now() ELSE resolved_at END WHERE id = $1",
                tid,
                ticket_status,
            )
            verb = "aprovado" if final == "approved" else "recusado"
            who = d.approver.display_name
            proto, title = row["protocol"], row["title"]
            await outbox.write(
                c,
                outbox.audit(
                    who,
                    f"Chamado {verb} pela Inbox",
                    proto,
                    f"{title} · {d.comment}" if d.comment else title,
                ),
            )
            await outbox.write(
                c,
                outbox.notification(
                    who,
                    f"Chamado {proto} {verb}",
                    f"{who} {verb} o seu pedido: {title}",
                    f"/apps/servicos/requests?protocolo={url_enc(proto)}",
                    to_users=[row["requested_by"]],
                    level="success" if final == "approved" else "warning",
                ),
            )
        return final

    return decide


# ---- app -----------------------------------------------------------------------------------------
class State:
    def __init__(self, sdk: HubSdk, scratch: Path, sessions: Sessions) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.sessions = sessions
        self.templates: dict[str, str] = {}
        self.counts: tuple[float, dict[str, int]] | None = None

    def raw(self, name: str) -> str | None:
        body = self.templates.get(name)
        if body is None:
            try:
                body = (HERE / "fragments" / f"{name}.html").read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        return body

    def skeleton(self, req: Request) -> str | None:
        body = self.raw("_skeleton")
        return None if body is None else personalize(body, user_of(req), from_headers(req.headers))

    def page(self, section: str, req: Request, active: str | None = None) -> str | None:
        """Fragmento de ``section`` com o contexto do hub e o item "Relatórios" na sidebar
        (``active`` marca o item ativo das páginas extras)."""
        body = self.raw(section)
        if body is None:
            return None
        body = personalize(body, user_of(req), from_headers(req.headers))
        return views.inject_nav(body, active or section)

    async def status_counts(self) -> dict[str, int]:
        """Contagem por status (cache de 15 s: um ``GROUP BY`` em milhões de linhas por página
        vista seria desperdício)."""
        if self.counts is not None and time.monotonic() - self.counts[0] < COUNT_TTL_S:
            return self.counts[1]
        out: dict[str, int] = {}
        try:
            async with self.sdk.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT status, count(*) FROM tickets WHERE status IN ('Aberto','Em atendimento','Aguardando') "
                    "GROUP BY status"
                )
            out = {r[0]: r[1] for r in rows}
        except DBERR:
            pass
        self.counts = (time.monotonic(), out)
        return out


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, after_migrate=seed)
    sdk.search = make_search(sdk)
    sdk.approvals = make_approvals(sdk)
    st = State(sdk, scratch, Sessions.from_env())
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.servicos = st
    e2e_hostile.install(app, APP_ID)  # só com HUB_DEMO_E2E_HOSTILE=1 (testes)
    register(app, st)
    # Onda 11: API v1 (OpenAPI em /servicos/api/openapi.json; MCP interno gerado dele).
    from servicos.api import register_api

    register_api(app, st)
    return app


def user_of(req: Request) -> str:
    return identity_of(req).display_name


def transient(e: BaseException) -> bool:
    return isinstance(
        e,
        OSError
        | asyncpg.PostgresConnectionError
        | asyncpg.InterfaceError
        | asyncpg.CannotConnectNowError
        | asyncpg.TooManyConnectionsError
        | TimeoutError,
    )


def db_notice(st: State) -> str | None:
    if st.sdk.db is None:
        return notice(
            "info",
            "Banco de dados não configurado",
            "Defina HUB_DB_URL (ou HUB_DB_URL_SERVICOS) para usar os chamados reais. Exibindo os dados de exemplo do protótipo.",
        )
    if not st.sdk.db_ready:
        return notice(
            "warn",
            "Banco de dados indisponível",
            "O banco ainda está subindo ou não respondeu. Tente novamente em instantes.",
        )
    return None


def not_found() -> Response:
    return html(
        '<section class="empty-state"><h1>Página não encontrada</h1><p>Esta seção não existe. '
        '<a href="/apps/servicos">Voltar ao início do app</a>.</p></section>',
        404,
    )


def retry_after(resp: Response, secs: int = 2) -> Response:
    resp.headers["retry-after"] = str(secs)
    return resp


async def audit_tx(conn: Any, req: Request, action: str, target: str, detail: str) -> None:
    """Evento de auditoria + marca de idempotência na transação da ação."""
    await outbox.write(conn, outbox.audit(user_of(req), action, target, detail))
    ctx = idem_ctx(req)
    if ctx is not None:
        await ctx.mark_committed(conn)


def session_key(req: Request) -> tuple[str, str | None]:
    """Sessão do usuário: cookie ``svc_sid`` + usuário da identidade injetada. Devolve a chave e o
    sid novo (a gravar no cookie) quando o navegador ainda não tem um."""
    user = user_of(req)
    for line in req.headers.getlist("cookie"):
        for part in line.split(";"):
            sid = part.strip().removeprefix("svc_sid=") if part.strip().startswith("svc_sid=") else ""
            if sid and len(sid) <= 64 and sid.isascii() and sid.isalnum():
                return f"{user}|{sid}", None
    sid = secrets.token_hex(16)
    return f"{user}|{sid}", sid


def with_cookie(resp: Response, new_sid: str | None) -> Response:
    if new_sid is not None:
        resp.headers.append("set-cookie", f"svc_sid={new_sid}; Path=/; HttpOnly; SameSite=Lax")
    return resp


def register(app: FastAPI, st: State) -> None:
    def static(name: str, req: Request) -> Response:
        body = st.page(name, req) if name in SECTIONS else None
        return html(body) if body is not None else not_found()

    @app.get("/servicos")
    @app.get("/servicos/")
    async def dashboard(req: Request) -> Response:
        return static("dashboard", req)

    @app.get("/servicos/_skeleton")
    async def skeleton(req: Request) -> Response:
        body = st.skeleton(req)
        return html(body) if body is not None else not_found()

    @app.get("/servicos/assets/{name}")
    async def asset(name: str) -> Response:
        if name == "demo.css":
            p = shared_assets_dir() / "demo.css"
        elif name == "app.css":
            p = HERE / "assets" / "app.css"
        else:
            return Response(status_code=404)
        try:
            body = p.read_bytes()
        except OSError:
            return Response(status_code=404)
        return Response(body, media_type=CSS, headers={"cache-control": "no-cache"})

    # ---- lista, abertura e atualização -----------------------------------------------------------
    @app.get("/servicos/requests")
    async def requests_page(req: Request) -> Response:
        key, new_sid = session_key(req)
        q = dict(req.query_params)
        st.sessions.touch(key, lambda s: setattr(s, "last_filters", repr(q)))
        n = db_notice(st)
        if n is not None:
            t = st.page("requests", req) or ""
            marker = '<div class="app3-request-stats">'
            return with_cookie(html(t.replace(marker, f"{n}{marker}", 1)), new_sid)
        status = pick(param(q, "status"), STATUSES)
        cat = pick(param(q, "categoria"), CATEGORIES)
        pri = pick(param(q, "prioridade"), PRIORITIES)
        proto = param(q, "protocolo")[:40]
        before = to_i64(param(q, "antes"))
        conds: list[str] = []
        ps: list[Any] = []
        for col, v in (("status", status), ("category", cat), ("priority", pri)):
            if v:
                ps.append(v)
                conds.append(f"{col} = ${len(ps)}")
        if proto:
            ps.append(proto.upper())
            conds.append(f"protocol = ${len(ps)}")
        if before is not None:
            ps.append(before)
            conds.append(f"id < ${len(ps)}")
        wh = f"WHERE {' AND '.join(conds)}" if conds else ""
        rows: list[Any] | None
        err = ""
        try:
            async with st.sdk.pool.acquire() as c:
                rows = list(
                    await c.fetch(
                        f"SELECT {TICKET_COLS} FROM tickets {wh} ORDER BY id DESC LIMIT {PAGE}", *ps
                    )
                )
        except DBERR as e:
            rows, err = None, str(e)
        body = views.heading_and_stats(await st.status_counts())
        if param(q, "new") == "1":
            body += views.new_form()
        body += views.filters(proto, status, cat, pri)
        if rows is not None:
            body += views.table(rows, views.base_query(status, cat, pri, proto), before is not None)
        else:
            body += notice("error", "Erro ao consultar os chamados", err)
        tpl = st.page("requests", req) or ""
        return with_cookie(html(wrap(tpl, f'<div class="app3-page">{body}</div>')), new_sid)

    @app.post("/servicos/requests")
    async def open_ticket(req: Request) -> Response:
        n = db_notice(st)
        if n is not None:
            return html(views.new_form(n), 503)
        f = {k: str(v) for k, v in (await req.form()).items()}
        title = param(f, "title")[:160]
        cat = pick(param(f, "category"), CATEGORIES)
        pri = pick(param(f, "priority"), PRIORITIES)
        desc = param(f, "description")[:4000]
        if not title or not cat or not pri or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
            msg = notice("error", "Verifique os campos", "Título, categoria e prioridade são obrigatórios.")
            return html(views.new_form(msg), 422)
        user = user_of(req)
        login = identity_of(req).user or user
        needs_approval = param(f, "approval") == "1"
        first = "Aguardando" if needs_approval else "Aberto"
        due = utcnow() + sla_for(pri)
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                row = await c.fetchrow(INSERT_SQL, title, user, cat, pri, desc, due, first)
                proto, tid = row["protocol"], row["id"]
                await audit_tx(c, req, "Chamado aberto", proto, f"{cat} · {pri} · {title}")
                if needs_approval:
                    await request_approval(c, tid, proto, title, login, user)
        except DBERR as e:
            msg = notice("error", "Não foi possível abrir o chamado", str(e))
            return html(views.new_form(msg), 503)
        extra = " · aguardando aprovação na Inbox" if needs_approval else ""
        return html(
            '<section class="app3-panel hub-panel" id="ticket-new">'
            + notice("ok", "Chamado aberto", f"Protocolo {proto} · SLA até {due:%d/%m %H:%M}{extra}")
            + '<div class="modal-actions"><a class="secondary-button" '
            f'href="/apps/servicos/requests?protocolo={url_enc(proto)}">Ver chamado</a>'
            '<a class="app3-primary" href="/apps/servicos/requests?new=1">Abrir outro</a></div></section>',
            201,
        )

    async def load(tid: int) -> Any:
        async with st.sdk.pool.acquire() as c:
            return await c.fetchrow(f"SELECT {TICKET_COLS} FROM tickets WHERE id = $1", tid)

    @app.api_route("/servicos/requests/{tid}", methods=["POST", "GET"])
    async def update_ticket(req: Request, tid: str) -> Response:
        ident = to_i64(tid)
        if req.method == "GET" or ident is None:
            return not_found()
        n = db_notice(st)
        if n is not None:
            return html(n, 503)
        f = {k: str(v) for k, v in (await req.form()).items()}
        status = pick(param(f, "status"), STATUSES)
        sent = to_i64(param(f, "version"))
        sent = sent if sent is not None and sent < 2**31 else 0

        def cell(msg: str) -> str:
            return f'<div class="hub-row-msg">{msg}</div>'

        if not status:
            return html(notice("error", "Status inválido", ""), 422)
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                row = await c.fetchrow(UPDATE_SQL, ident, sent, status)
                if row is not None:
                    await audit_tx(c, req, "Chamado atualizado", row["protocol"], f"status: {status}")
        except DBERR as e:
            if transient(e):
                msg = notice(
                    "error", "Banco indisponível", "Nada foi confirmado. Tente novamente em instantes."
                )
                return retry_after(html(msg, 503))
            return html(notice("error", "Erro ao atualizar", str(e)), 500)
        if row is not None:
            return html(views.row_html(row, cell(notice("ok", "Atualizado", f"Versão {row['version']}"))))
        try:
            cur = await load(ident)
        except DBERR:
            cur = None
        if cur is None:
            return html(notice("warn", "Chamado não encontrado", ""), 404)
        msg = notice(
            "error",
            "Chamado alterado por outra pessoa",
            f"Você atualizou a versão {sent}, mas a atual é {cur['version']} ({cur['status']}). "
            "Nada foi gravado; revise e tente de novo.",
        )
        return html(views.row_html(cur, cell(msg)), 409)

    # ---- relatório gerencial -----------------------------------------------------------------------
    @app.get("/servicos/reports")
    async def reports(req: Request) -> Response:
        key, new_sid = session_key(req)
        q = dict(req.query_params)
        params = report.Params.from_query(q)
        body = views.reports_head(params)
        if param(q, "de"):
            n = db_notice(st)
            if n is not None:
                body += n
            else:
                cached = st.sessions.get_report(key, params.key())
                permit: Permit | None = None
                if cached is None:
                    permit = st.sdk.heavy.try_acquire()
                    if permit is None:
                        return with_cookie(busy("O relatório", "Relatório em fila…"), new_sid)
                try:
                    result = cached or await report.run(st.sdk.pool, params)
                except DBERR as e:
                    body += notice("error", "Erro ao gerar o relatório", str(e))
                else:
                    st.sessions.put_report(key, params.key(), result)
                    body += report.render(result, params, cached is not None)
                finally:
                    if permit is not None:
                        permit.release()
        else:
            body += notice(
                "info",
                "Escolha o período",
                "O relatório agrega todos os chamados do período (milhões de linhas); cada sessão guarda o último resultado em cache.",
            )
        s = st.sessions.stats()
        body += (
            '<p class="hub-muted hub-session-stats">'
            f"Cache de sessões: {thousands(s.sessions)} de {thousands(s.capacity)} usuários ativos · "
            f"~{thousands(s.approx_bytes // 1024)} KiB em memória · {st.sdk.heavy.in_use} operações pesadas "
            f"em andamento (limite {st.sdk.heavy.max}).</p>"
        )
        tpl = st.page("requests", req, "reports") or ""
        return with_cookie(html(wrap(tpl, f'<div class="app3-page">{body}</div>')), new_sid)

    @app.get("/servicos/reports/export.csv")
    async def export_csv(req: Request) -> Response:
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

    @app.get("/servicos/{section}")
    async def other(req: Request, section: str) -> Response:
        return static(section, req)
