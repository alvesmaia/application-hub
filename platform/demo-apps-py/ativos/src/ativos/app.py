"""Asset management — the platform's FastAPI app (``HUB_DEMO_APP=ativos``; same routes, same markup, same database).

Routes (all under ``/ativos``; the hub exposes them at ``/_frag/ativos/...``):

- ``GET /ativos`` · ``/ativos/{dashboard,assets,movements,reports,settings}`` — fragmentos
  (app workspace: sidebar + content). ``assets`` is dynamic (Postgres): paginated list with
  search, filters and server-side sorting, panels ``?edit=<id>``, ``?new=1``, ``?import=1``;
  the other sections are the mockup fragments (``fragments/``);
- ``GET /ativos/_skeleton`` · ``/ativos/assets/{demo.css,app.css}`` (no JavaScript — wave 12);
- ``POST /ativos/assets`` (create) · ``POST /ativos/itens/{id}`` (save, with ``version`` →
  409 if someone else changed it first) · ``POST /ativos/itens/{id}/delete``;
- ``POST /ativos/itens/import`` (multipart) → durable job ``ativos.import``;
  ``GET …/import/{job}`` (progresso) · ``GET …/import/{job}/erros.csv``;
- ``GET /ativos/itens/export.csv`` (streaming, heavy operation) · ``POST …/export.xlsx`` →
  tarefa ``ativos.export_xlsx``; ``GET …/export/{job}`` · ``…/export/{job}/ativos.xlsx`` ·
  ``GET /ativos/itens/modelo.xlsx``;
- ``/ativos/api/v1/*`` (OpenAPI at ``/ativos/api/openapi.json``; MCP tools generated from it)
  and ``/ativos/_hub/*`` (hub-sdk internal endpoints, internal token).

Every action that writes data also writes the audit event to the outbox and the
idempotency marker **in the same transaction**.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from functools import partial
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from hub_sdk import e2e_hostile, flags, outbox, telemetry
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.heavy import PermitStreamingResponse
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.jobs import Handler, Job, enqueue_with
from hub_sdk.locale import from_headers
from hub_sdk.render import BOM, busy, csv_line, download_headers, html
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score
from starlette.datastructures import UploadFile

from ativos import exporter, importer, saga, views
from ativos.i18n import I18nMiddleware, personalize
from ativos.model import COLS, EDIT_FIELDS, MAX_PAGE, PAGE_SIZE, SCHEMA_SQL, SEED_SQL, validate
from ativos.query import Filter
from ativos.ui import esc, notice, param, query, thousands, wrap

log = logging.getLogger("ativos")

APP_ID = "ativos"
HERE = Path(__file__).resolve().parent
FLAG_NOVO_RESUMO = "novo-resumo"
ASSETS_BASE = 300_000
SEED_CHUNK = 50_000
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]
TEXT_COLS = ", ".join(f"{c.key}::text AS {c.key}" for c in COLS)
CSS = "text/css; charset=utf-8"

NOUNS = ("ativo", "ativos", "asset", "assets", "activo", "activos", "patrimonio", "inventario")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def import_max_mb() -> int:
    """Maximum size of the ``.xlsx`` (``HUB_IMPORT_MAX_MB``, default 50); the gateway applies the
    ``limits.max_upload_mb`` from the manifest first."""
    return _int_env("HUB_IMPORT_MAX_MB", 50)


def shared_assets_dir() -> Path:
    """Shared ``demo.css``: ``ATIVOS_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("ATIVOS_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def seed_rows() -> int:
    """Seed rows: ``ATIVOS_SEED_ROWS`` or 300k × ``HUB_SEED_SCALE``."""
    if os.environ.get("ATIVOS_SEED_ROWS", ""):
        return _int_env("ATIVOS_SEED_ROWS", 0)
    try:
        scale = float(os.environ.get("HUB_SEED_SCALE", "1"))
    except ValueError:
        scale = 1.0
    return round(ASSETS_BASE * scale) if scale >= 0 else ASSETS_BASE


async def seed_into(c: Any, rows: int) -> None:
    """Idempotent, resumable seed load: batches of 50k generated in Postgres; a batch already present is
    skipped (its last row's asset tag is checked)."""
    t0 = time.monotonic()
    for start in range(1, rows + 1, SEED_CHUNK):
        end = min(start + SEED_CHUNK - 1, rows)
        last = f"PT-{2016 + end % 10}-{end:07d}"
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM assets WHERE tag = $1)", last):
            await c.execute(SEED_SQL, start, end)
    await c.execute("ANALYZE assets")
    log.warning("ativos: carga de %d ativos pronta (%.0f s)", rows, time.monotonic() - t0)


async def seed(sdk: HubSdk) -> None:
    rows = seed_rows()
    if rows <= 0 or sdk.pool is None:
        return
    async with sdk.pool.acquire() as c:
        await c.execute("SELECT pg_advisory_lock(424244)")
        try:
            await seed_into(c, rows)
        finally:
            await c.execute("SELECT pg_advisory_unlock(424244)")


async def run_seed() -> int:
    """``python -m ativos seed``: migra o esquema e faz a carga (300 mil × ``HUB_SEED_SCALE``) fora
    of the app — the equivalent of the one-shot ``hub-seed`` service. Idempotent and resumable."""
    from hub_sdk.db import app_env

    url = app_env(APP_ID, "HUB_DB_URL")
    if url is None:
        print("defina HUB_DB_URL (ou HUB_DB_URL_ATIVOS)", flush=True)
        return 2
    c = await asyncpg.connect(url)
    try:
        await c.execute(SCHEMA_SQL)
        await seed_into(c, seed_rows())
    finally:
        await c.close()
    return 0


def build_sdk(scratch: Path) -> HubSdk:
    """App SDK (the same in the web process and in the jobs process)."""
    pool = saga.LazyPool()
    handlers: list[Handler] = [importer.ImportHandler(), exporter.ExportHandler(scratch)]
    sg = saga.build(pool)
    handlers.append(sg)  # type: ignore[arg-type]
    if saga.storage.connection(saga.DOCS) is not None:
        from hub_sdk.uploads import PromoteHandler

        up = saga.uploads_for(pool)
        if up is not None:
            handlers.append(PromoteHandler(up))

    async def sweep(sdk: HubSdk) -> None:
        await sweep_scratch(sdk, scratch)
        up2 = saga.uploads_for(sdk.pool) if sdk.pool is not None else None
        if up2 is not None:
            with contextlib.suppress(Exception):
                await up2.cleanup()

    sdk = HubSdk.from_env(
        APP_ID, app_sql=SCHEMA_SQL, handlers=handlers, after_migrate=seed, cleanup_hooks=[sweep]
    )
    pool.sdk = sdk
    return sdk


async def sweep_scratch(sdk: HubSdk, scratch: Path) -> int:
    """Cleanup of ``/scratch`` (at startup and every hour): exports older than 6 h, ``.part``
    orphans and import uploads that no active job has used for more than 1 h."""
    active: set[str] | None = None
    if sdk.db_ready and sdk.pool is not None:
        try:
            async with sdk.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT payload->>'file_path' AS p FROM hub_jobs WHERE kind = $1 AND status IN ('queued', 'running')",
                    importer.KIND,
                )
            active = {str(r["p"]) for r in rows if r["p"]}
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("ativos: limpeza do scratch sem banco: %s", e)
    n = await asyncio.to_thread(exporter.cleanup_old_exports, scratch, 6 * 3600, active)
    if n:
        log.warning("ativos: limpeza do scratch: %d arquivo(s) antigo(s) apagado(s)", n)
    return n


async def run_worker() -> None:
    """``python -m ativos worker``: jobs process (``HUB_JOBS_MODE=process``)."""
    await build_sdk(scratch_dir(APP_ID)).run_worker_process()


def fold(s: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """Is the search the entity name ("ativos", "assets", "patrimônio")? Then the most recent ones
    answer (global search never comes back empty for the app)."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


def make_search(sdk: HubSdk) -> Any:
    """Federated search (wave 9): name, asset tag, category or location →
    ``/apps/ativos/assets?q=<patrimônio>``, label in the searcher's language."""

    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        generic = generic_term(q.q)
        async with sdk.pool.acquire() as c:
            if generic:
                rows = await c.fetch(
                    "SELECT tag, name, category, location, status FROM assets ORDER BY id DESC LIMIT $1",
                    q.limit,
                )
            else:
                rows = await c.fetch(
                    "SELECT tag, name, category, location, status FROM assets WHERE name ILIKE $1 OR tag ILIKE $1 "
                    "OR category ILIKE $1 OR location ILIKE $1 ORDER BY (tag ILIKE $2) DESC, (name ILIKE $1) DESC, "
                    "name LIMIT $3",
                    like_pattern(q.q),
                    q.q,
                    q.limit,
                )
        kind = {"en": "Asset"}.get(q.locale, "Ativo")
        return [
            SearchHit(
                title=r["name"],
                subtitle=f"{r['tag']} · {r['category']} · {r['location']}",
                url=f"/apps/ativos/assets{query([('q', r['tag'])])}",
                kind=kind,
                score=0.5
                if generic
                else max(score(r["name"], q.q), score(r["tag"], q.q), score(r["category"], q.q) * 0.7, 0.3),
            )
            for r in rows
        ]

    return search


class State:
    def __init__(self, sdk: HubSdk, scratch: Path) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.templates: dict[str, str] = {}

    def fragment(self, name: str, req: Request) -> str | None:
        """Static mockup fragment with the hub context (user, language, date, app name)."""
        body = self.templates.get(name)
        if body is None:
            p = HERE / "fragments" / f"{name}.html"
            try:
                body = p.read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        return personalize(body, user_of(req), from_headers(req.headers))


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = build_sdk(scratch)
    sdk.search = make_search(sdk)
    st = State(sdk, scratch)
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.ativos = st
    e2e_hostile.install(app, APP_ID)  # only with HUB_DEMO_E2E_HOSTILE=1 (tests)
    register(app, st)
    # Wave 11: API v1 (OpenAPI at /ativos/api/openapi.json; internal MCP generated from it).
    from ativos.api import register_api

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
            "Defina HUB_DB_URL (ou HUB_DB_URL_ATIVOS) para usar o cadastro real. Exibindo os dados de exemplo do protótipo.",
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
        '<a href="/apps/ativos">Voltar ao início do app</a>.</p></section>',
        404,
    )


def retry_after(resp: Response, secs: int = 2) -> Response:
    resp.headers["retry-after"] = str(secs)
    return resp


async def audit_tx(conn: Any, req: Request, action: str, target: str, detail: str) -> None:
    """Audit event + idempotency marker in the action's transaction."""
    await outbox.write(conn, outbox.audit(user_of(req), action, target, detail))
    ctx = idem_ctx(req)
    if ctx is not None:
        await ctx.mark_committed(conn)


async def load_asset(st: State, aid: int) -> Any:
    async with st.sdk.pool.acquire() as c:
        return await c.fetchrow(f"SELECT id, version, {TEXT_COLS} FROM assets WHERE id = $1", aid)


async def list_assets(st: State, f: Filter) -> tuple[int, list[Any]]:
    wh, params = f.where()
    async with st.sdk.pool.acquire() as c:
        # Wave 8: "db …" spans as children of the request span, and traceparent in the SQL comment.
        tc = telemetry.sql_comment()
        async with telemetry.db("SELECT assets (contagem)"):
            total = int(await c.fetchval(f"{tc}SELECT count(*) FROM assets {wh}", *params))
        async with telemetry.db("SELECT assets (página)"):
            rows = await c.fetch(
                f"{tc}SELECT id, tag, name, category, location, status, acquisition_value_cents, acquisition_date "
                f"FROM assets {wh} ORDER BY {f.order()} LIMIT {PAGE_SIZE} OFFSET {(f.page - 1) * PAGE_SIZE}",
                *params,
            )
    return total, list(rows)


def conflict(current: int, sent: int) -> str:
    return notice(
        "error",
        "Este ativo foi alterado por outra pessoa",
        f"Você editou a versão {sent}, mas a versão atual é {current}. Suas alterações não foram salvas. "
        "Recarregue o ativo para ver os dados atuais e edite novamente.",
    )


def import_form(msg: str = "") -> str:
    return views.import_form(msg, import_max_mb(), importer.MAX_ROWS)


def register(app: FastAPI, st: State) -> None:  # noqa: C901, PLR0915
    def dashboard_page(req: Request) -> Response:
        body = st.fragment("dashboard", req)
        if body is None:
            return not_found()
        # Wave 8 — demo feature flag `novo-resumo` (Platform › Feature flags): the
        # hub evaluates it for the user and sends it in `x-hub-flags`.
        if flags.from_headers(req.headers).is_on(FLAG_NOVO_RESUMO):
            banner = (
                '<div class="hub-flag-banner" data-flag="novo-resumo" role="note"><strong>Novo resumo</strong> — 1.106 '
                "ativos em uso, 23 em manutenção e 3 precisando de atenção hoje. "
                "<small>(recurso liberado por feature flag)</small></div>"
            )
            body = body.replace('<div class="app-page-header">', f'{banner}<div class="app-page-header">', 1)
        return html(body)

    @app.get("/ativos")
    @app.get("/ativos/")
    @app.get("/ativos/dashboard")
    async def dashboard(req: Request) -> Response:
        return dashboard_page(req)

    @app.get("/ativos/_skeleton")
    async def skeleton(req: Request) -> Response:
        body = st.fragment("_skeleton", req)
        return html(body) if body is not None else not_found()

    @app.get("/ativos/assets/{name}")
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

    # ---- list, registration and editing --------------------------------------------------------
    @app.get("/ativos/assets")
    async def assets_page(req: Request) -> Response:
        n = db_notice(st)
        if n is not None:
            # No DB: the static mockup fragment, with the notice at the top.
            t = st.fragment("assets", req) or ""
            return html(t.replace('<div class="app-page-header">', f'{n}<div class="app-page-header">', 1))
        q = dict(req.query_params)
        f = Filter.from_query(q)
        panel = ""
        edit = param(q, "edit")
        try:
            if re.fullmatch(r"-?\d+", edit):
                row = await load_asset(st, int(edit))
                panel = (
                    views.editor(row, "", f.qs())
                    if row
                    else notice("warn", "Ativo não encontrado", "Ele pode ter sido excluído.")
                )
            elif param(q, "new") == "1":
                panel = views.creator({})
            elif param(q, "import") == "1":
                panel = import_form()
            elif param(q, "job"):
                j = await get_job(st, param(q, "job"), importer.KIND)
                if j is not None:
                    panel = views.job_panel(importer.job_view(j))
        except (OSError, asyncpg.PostgresError) as e:
            panel = notice("error", "Erro ao carregar o ativo", str(e))
        try:
            total, rows = await list_assets(st, f)
            body = (
                views.page_header(
                    f"{thousands(total)} ativos encontrados. Busca, filtros e ordenação no servidor.",
                    views.header_actions(f),
                )
                + panel
                + '<div id="export-panel"></div>'
                + views.table(f, total, rows)
            )
        except (OSError, asyncpg.PostgresError) as e:
            body = views.page_header("Inventário", views.header_actions(f)) + notice(
                "error", "Erro ao consultar os ativos", str(e)
            )
        tpl = st.fragment("assets", req) or ""
        return html(wrap(tpl, body))

    def values_from(form: Any) -> dict[str, str]:
        return {k: str(v) for k, v in form.items()}

    @app.post("/ativos/assets")
    async def create(req: Request) -> Response:
        form = values_from(await req.form())
        if (n := db_notice(st)) is not None:
            return html(views.creator(form, n), 503)
        vals, errs = validate(form, EDIT_FIELDS)
        if errs:
            return html(views.creator(form, notice("error", "Verifique os campos", errs)), 422)
        tag = vals["tag"]
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                aid = await c.fetchval(
                    "INSERT INTO assets (tag, name, category, location, status, responsible, department, "
                    "acquisition_date, acquisition_value_cents, notes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                    "RETURNING id",
                    *[vals[k] for k in EDIT_FIELDS],
                )
                await audit_tx(c, req, "Ativo cadastrado", tag, vals["name"] or "")
        except asyncpg.UniqueViolationError:
            msg = notice("error", "Patrimônio já cadastrado", "Use outro número de patrimônio.")
            return html(views.creator(form, msg), 409)
        except (OSError, asyncpg.PostgresError) as e:
            if transient(e):
                msg = notice(
                    "error", "Banco indisponível", "Nada foi confirmado. Tente novamente em instantes."
                )
                return retry_after(html(views.creator(form, msg), 503))
            return html(
                views.creator(form, notice("error", "Erro ao cadastrar", importer.db_error_text(e))), 500
            )
        row = await load_asset(st, aid)
        ok = notice("ok", "Ativo cadastrado", f"Patrimônio {tag} criado.")
        return html(views.editor(row, ok) if row else notice("ok", "Ativo cadastrado", tag), 201)

    # ---- import ------------------------------------------------------------------------
    @app.api_route("/ativos/itens/import", methods=["POST", "GET"])
    async def start_import(req: Request) -> Response:  # noqa: C901, PLR0912, PLR0915
        if req.method == "GET":
            return not_found()
        if (n := db_notice(st)) is not None:
            return html(import_form(n), 503)

        def fail(status: int, title: str, text: str) -> Response:
            return html(import_form(notice("error", title, text)), status)

        # The upload is only written to disk; the heavy-operation slot is taken by the worker (without
        # a slot, the job waits in the queue instead of getting a 429).
        try:
            form = await req.form(max_files=1, max_fields=10)
        except Exception:
            return fail(400, "Envio interrompido", "O arquivo não pôde ser lido.")
        up = form.get("file")
        if not isinstance(up, UploadFile) or up.filename in (None, ""):
            return fail(422, "Nenhum arquivo enviado", "Selecione uma planilha .xlsx.")
        name = "".join(ch for ch in up.filename if ch.isprintable())[:120] or "planilha.xlsx"
        if not name.lower().endswith(".xlsx"):
            return fail(422, "Formato não suportado", "Envie um arquivo .xlsx (Excel 2007 ou superior).")
        uid = f"{int(time.time() * 1000):x}{secrets.token_hex(2)}"
        dest = st.scratch / f"upload-{uid}.xlsx"
        report = st.scratch / f"import-{uid}-erros.csv"
        size, limit = 0, import_max_mb() * 1024 * 1024
        try:
            out = await asyncio.to_thread(partial(dest.open, "wb"))
            try:
                first = True
                while chunk := await up.read(1024 * 1024):
                    if first and not chunk.startswith(b"PK\x03\x04"):
                        raise ValueError("not-xlsx")
                    first = False
                    size += len(chunk)
                    if size > limit:
                        raise OverflowError
                    await asyncio.to_thread(out.write, chunk)
            finally:
                await asyncio.to_thread(out.close)
        except ValueError:
            dest.unlink(missing_ok=True)
            return fail(422, "Arquivo inválido", "O conteúdo não é uma planilha .xlsx.")
        except OverflowError:
            dest.unlink(missing_ok=True)
            return fail(413, "Arquivo muito grande", f"O limite é {import_max_mb()} MB.")
        finally:
            await up.close()
        if size == 0:
            dest.unlink(missing_ok=True)
            return fail(422, "Nenhum arquivo enviado", "Selecione uma planilha .xlsx.")
        ctx = idem_ctx(req)
        payload = {
            "file": name,
            "file_path": str(dest),
            "report_path": str(report),
            "login": identity_of(req).user,
        }
        # Job + audit + idempotency marker in the same transaction.
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                job, created = await enqueue_with(
                    c, importer.SPEC, payload, user_of(req), ctx.key if ctx else None
                )
                if created:
                    await audit_tx(c, req, "Importação de ativos iniciada", name, f"{size // 1024} KB")
                elif ctx is not None:
                    await ctx.mark_committed(c)
        except (OSError, asyncpg.PostgresError) as e:
            dest.unlink(missing_ok=True)
            return fail(503, "Não foi possível registrar a importação", str(e))
        if not created:
            dest.unlink(missing_ok=True)  # resubmission of the same submission: the job already exists
        st.sdk.wake()
        return html(views.job_panel(importer.job_view(job)), 202)

    @app.get("/ativos/itens/import/{job_id}")
    async def import_status(job_id: str) -> Response:
        if not jobs_ready(st):
            n = db_notice(st) or ""
            return html(
                '<section class="content-panel hub-panel" id="import-panel" '
                f'data-hub-poll="/apps/ativos/itens/import/{esc(job_id)}" data-hub-interval="3000">{n}</section>',
                503,
            )
        j = await get_job(st, job_id, importer.KIND)
        if j is None:
            msg = notice("warn", "Importação não encontrada", "Confira o link ou envie a planilha novamente.")
            return html(f'<section class="content-panel hub-panel" id="import-panel">{msg}</section>', 404)
        return html(views.job_panel(importer.job_view(j)))

    @app.get("/ativos/itens/import/{job_id}/erros.csv")
    async def import_errors(job_id: str) -> Response:
        j = await get_job(st, job_id, importer.KIND) if jobs_ready(st) else None
        if j is None:
            return not_found()
        p = Path(str((j.payload or {}).get("report_path", "")))
        if not await asyncio.to_thread(p.is_file):
            return not_found()
        return FileResponse(
            p, headers=download_headers("text/csv; charset=utf-8", f"erros-importacao-{j.id}.csv")
        )

    @app.get("/ativos/itens/modelo.xlsx")
    async def template_xlsx() -> Response:
        p = st.scratch / "modelo-ativos.xlsx"
        if not p.exists():
            await asyncio.to_thread(importer.template_xlsx, p)
        return FileResponse(p, headers=download_headers(XLSX, "modelo-ativos.xlsx"))

    # ---- export ------------------------------------------------------------------------
    @app.get("/ativos/itens/export.csv")
    async def export_csv(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(n, 503)
        permit = st.sdk.heavy.try_acquire()
        if permit is None:
            return busy("A exportação", "Exportação em fila…")
        f = Filter.from_query(req.query_params)
        await st.sdk.emit(user_of(req), "Exportação de ativos (CSV)", "ativos.csv", f.describe())
        # The slot is returned at the end of the send, on client disconnect, or when the object is gone.
        return PermitStreamingResponse(
            exporter.csv_stream(st.sdk.pool, f),
            permit,
            headers=download_headers("text/csv; charset=utf-8", "ativos.csv"),
        )

    @app.api_route("/ativos/itens/export.xlsx", methods=["POST", "GET"])
    async def export_xlsx(req: Request) -> Response:
        if req.method == "GET":
            return not_found()
        if (n := db_notice(st)) is not None:
            return html(n, 503)
        if st.sdk.jobs is None:
            return export_error("tarefas indisponíveis")
        form = values_from(await req.form())
        f = Filter.from_query(form)
        ctx = idem_ctx(req)
        await asyncio.to_thread(exporter.cleanup_old_exports, st.scratch)
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                job, _ = await enqueue_with(
                    c, exporter.SPEC, f.payload(), user_of(req), ctx.key if ctx else None
                )
                if ctx is not None:
                    await ctx.mark_committed(c)
        except (OSError, asyncpg.PostgresError) as e:
            return export_error(str(e))
        st.sdk.wake()
        return html(exporter.panel(job), 202)

    @app.get("/ativos/itens/export/{job_id}")
    async def export_status(job_id: str) -> Response:
        if not jobs_ready(st):
            return export_error("banco indisponível")
        j = await get_job(st, job_id, exporter.KIND)
        if j is None:
            return html(notice("warn", "Exportação não encontrada", "Gere a exportação de novo."), 404)
        return html(exporter.panel(j))

    @app.get("/ativos/itens/export/{job_id}/ativos.xlsx")
    async def export_download(job_id: str) -> Response:
        j = await get_job(st, job_id, exporter.KIND) if jobs_ready(st) else None
        if j is None or j.status != "done":
            return not_found()
        p = exporter.out_path(st.scratch, j.id)
        if not await asyncio.to_thread(p.is_file):
            msg = notice(
                "warn",
                "Arquivo expirado",
                "O arquivo desta exportação já foi removido. Gere a exportação de novo.",
            )
            return html(msg, 410)
        return FileResponse(p, headers=download_headers(XLSX, "ativos.xlsx"))

    # Routes with a parameter go last: `/itens/import` and `/itens/export.*` take precedence.
    @app.api_route("/ativos/itens/{aid}", methods=["POST", "GET"])
    async def update(req: Request, aid: str) -> Response:  # noqa: C901, PLR0915
        if req.method == "GET" or not aid.isdigit():
            return not_found()
        aid_n = int(aid)
        if (n := db_notice(st)) is not None:
            return html(n, 503)
        form = values_from(await req.form())
        try:
            sent = int(form.get("version", "0"))
        except ValueError:
            sent = 0
        try:
            current = await load_asset(st, aid_n)
        except (OSError, asyncpg.PostgresError) as e:
            return html(notice("error", "Banco indisponível", str(e)), 503)
        if current is None:
            msg = notice("warn", "Ativo não encontrado", "Ele pode ter sido excluído por outra pessoa.")
            return html(msg, 404)
        keys = tuple(k for k in EDIT_FIELDS if k != "tag")
        vals, errs = validate(form, keys)
        if errs:
            return html(views.editor(current, notice("error", "Verifique os campos", errs)), 422)
        tag = views.asset_values(current)["tag"]
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                version = await c.fetchval(
                    "UPDATE assets SET name=$3, category=$4, location=$5, status=$6, responsible=$7, department=$8, "
                    "acquisition_date=$9, acquisition_value_cents=$10, notes=$11, version = version + 1, "
                    "updated_at = now() WHERE id = $1 AND version = $2 RETURNING version",
                    aid_n,
                    sent,
                    *[vals[k] for k in keys],
                )
                if version is not None:
                    await audit_tx(c, req, "Ativo atualizado", tag, f"versão {sent} → {version}")
        except (OSError, asyncpg.PostgresError) as e:
            if transient(e):
                msg = notice(
                    "error", "Banco indisponível", "Nada foi confirmado. Tente novamente em instantes."
                )
                return retry_after(html(views.editor(current, msg), 503))
            return html(
                views.editor(current, notice("error", "Erro ao salvar", importer.db_error_text(e))), 500
            )
        if version is None:
            # Outdated version (or the asset is gone): 409 with the message.
            latest = await load_asset(st, aid_n)
            if latest is None:
                msg = notice(
                    "error", "Este ativo foi excluído por outra pessoa", "Suas alterações não foram salvas."
                )
                return html(msg, 409)
            return html(views.editor(latest, conflict(latest["version"], sent)), 409)
        row = await load_asset(st, aid_n) or current
        ok = notice("ok", "Alterações salvas", f"Versão {row['version']} gravada.")
        return html(views.editor(row, ok))

    @app.api_route("/ativos/itens/{aid}/delete", methods=["POST", "GET"])
    async def delete(req: Request, aid: str) -> Response:
        if req.method == "GET" or not aid.isdigit():
            return not_found()
        aid_n = int(aid)
        if (n := db_notice(st)) is not None:
            return html(n, 503)
        form = values_from(await req.form())
        try:
            sent = int(form.get("version", "0"))
        except ValueError:
            sent = 0
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                row = await c.fetchrow(
                    "DELETE FROM assets WHERE id = $1 AND version = $2 RETURNING tag, name", aid_n, sent
                )
                if row is not None:
                    await audit_tx(c, req, "Ativo excluído", row["tag"], row["name"])
        except (OSError, asyncpg.PostgresError) as e:
            if transient(e):
                msg = notice(
                    "error", "Banco indisponível", "Nada foi excluído. Tente novamente em instantes."
                )
                return retry_after(html(msg, 503))
            return html(notice("error", "Erro ao excluir", importer.db_error_text(e)), 500)
        if row is not None:
            return html(
                '<section class="content-panel hub-panel" id="asset-editor">'
                + notice("ok", "Ativo excluído", f"{row['tag']} · {row['name']}")
                + '<div class="modal-actions"><a class="primary-button" href="/apps/ativos/assets">Voltar à lista</a></div></section>'
            )
        cur = await load_asset(st, aid_n)
        if cur is not None:
            return html(views.editor(cur, conflict(cur["version"], sent)), 409)
        return html(notice("warn", "Ativo não encontrado", "Ele já foi excluído."), 404)

    @app.get("/ativos/{section}")
    async def other(req: Request, section: str) -> Response:
        if section not in SECTIONS:
            return not_found()
        if section == "dashboard":
            return dashboard_page(req)
        body = st.fragment(section, req)
        return html(body) if body is not None else not_found()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_error(e: str) -> Response:
    return html(notice("error", "Não foi possível exportar", e), 503)


def jobs_ready(st: State) -> bool:
    return st.sdk.db is not None and st.sdk.db_ready and st.sdk.jobs is not None


async def get_job(st: State, job_id: str, kind: str) -> Job | None:
    jobs = st.sdk.jobs
    if jobs is None or not jobs_ready(st) or len(job_id) > 80:
        return None
    with contextlib.suppress(OSError, asyncpg.PostgresError):
        j = await jobs.get(job_id)
        if j is not None and j.kind == kind:
            return j
    return None


_ = (COLS, MAX_PAGE, BOM, csv_line, StreamingResponse, AsyncIterator)
