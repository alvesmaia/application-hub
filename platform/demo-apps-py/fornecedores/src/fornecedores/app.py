"""Rede de Fornecedores — app FastAPI da plataforma (``HUB_DEMO_APP=fornecedores``; mesmas rotas e mesmo markup).

Rotas (todas sob ``/fornecedores``; o hub as expõe em ``/_frag/fornecedores/...``):

- ``GET /fornecedores`` · ``/fornecedores/{dashboard,indicators,analysis,reports,settings}`` —
  fragmentos do mockup (``fragments/``); ``indicators?id=FOR-0284`` insere a ficha do
  fornecedor (consulta cadastral + anexos) acima do resumo do diretório;
- ``GET /fornecedores/_skeleton`` · ``/fornecedores/assets/{demo.css,app.css}`` (sem JavaScript);
- ``POST /fornecedores/suppliers/{code}/lookup`` — consulta cadastral (API externa com
  timeout, novas tentativas e disjuntor);
- ``POST …/attachments`` (multipart) · ``GET …/attachments/{name}`` (download em streaming) ·
  ``POST …/attachments/{name}/delete`` — anexos em ``hub_sdk.storage`` (conexão
  ``fornecedores-anexos`` do hub ou bucket S3 próprio ``HUB_S3_*``);
- ``GET …/ficha.pdf`` — ficha em PDF (operação pesada: 429 sem vaga);
- ``/fornecedores/api/v1/*`` (OpenAPI em ``/fornecedores/api/openapi.json``; ferramentas MCP
  geradas dele) e ``/fornecedores/_hub/*`` (endpoints internos do hub-sdk, token interno).

Toda ação que altera dados grava um evento de auditoria no outbox do app (Postgres se houver
``HUB_DB_URL``; senão em memória) sem atrasar a resposta.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from hub_sdk import storage
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.db import app_env
from hub_sdk.identity import identity_of
from hub_sdk.locale import from_headers
from hub_sdk.render import busy, download_headers, html
from hub_sdk.search import SearchHit, SearchQuery, score
from starlette.datastructures import UploadFile

from fornecedores.ficha import ficha_pdf
from fornecedores.i18n import I18nMiddleware, personalize
from fornecedores.lookup import CnpjInfo, Lookup, Outcome
from fornecedores.suppliers import (
    SUPPLIERS,
    Supplier,
    attach_max_bytes,
    check_type,
    safe_name,
    supplier,
    valid_object_name,
)
from fornecedores.ui import esc, notice, param, thousands

log = logging.getLogger("fornecedores")

APP_ID = "fornecedores"
HERE = Path(__file__).resolve().parent
FRAG = "/_frag"
ATTACHMENTS_CONNECTION = "fornecedores-anexos"
CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]
NOUNS = ("fornecedor", "fornecedores", "supplier", "suppliers", "proveedor", "proveedores")


def shared_assets_dir() -> Path:
    """``demo.css`` compartilhado: ``FORNECEDORES_SHARED_ASSETS`` ou ``platform/demo-apps/assets``."""
    env = os.environ.get("FORNECEDORES_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def s3_config() -> storage.S3Config | None:
    """Bucket próprio do app (``HUB_S3_ENDPOINT``, ``HUB_S3_BUCKET``, chaves, região)."""
    endpoint, bucket = app_env(APP_ID, "HUB_S3_ENDPOINT"), app_env(APP_ID, "HUB_S3_BUCKET")
    ak, sk = app_env(APP_ID, "HUB_S3_ACCESS_KEY"), app_env(APP_ID, "HUB_S3_SECRET_KEY")
    if endpoint is None or bucket is None or ak is None or sk is None:
        return None
    return storage.S3Config(endpoint, bucket, ak, sk, app_env(APP_ID, "HUB_S3_REGION") or "us-east-1")


def attachments_store() -> storage.Connection | None:
    """Onde ficam os anexos: a conexão ``fornecedores-anexos`` (broker do hub) se vinculada; senão o
    bucket próprio do app; senão nenhum."""
    if (c := storage.connection(ATTACHMENTS_CONNECTION)) is not None:
        log.warning(
            "fornecedores: anexos na conexão %s (%s) pelo broker do hub", ATTACHMENTS_CONNECTION, c.kind
        )
        return c
    if (cfg := s3_config()) is None:
        return None
    try:
        return storage.s3_direct("s3-app", cfg)
    except storage.StorageError as e:
        log.error("fornecedores: object store: %s", e.message)
        return None


def fold(s: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn"
    )


def generic_term(q: str) -> bool:
    """A busca é o nome da entidade ("fornecedores", "suppliers")? Então todos respondem."""
    f = fold(q)
    return len(f) >= 4 and any(n.startswith(f) or f.startswith(n) for n in NOUNS)


def search_hits(q: SearchQuery) -> list[SearchHit]:
    kind = {"en": "Supplier"}.get(q.locale, "Fornecedor")
    generic = generic_term(q.q)
    hits = []
    for s in SUPPLIERS:
        sc = max(
            0.5 if generic else 0.0,
            score(s.name, q.q),
            score(s.code, q.q),
            score(s.category, q.q) * 0.7,
            score(s.city, q.q) * 0.6,
        )
        if sc > 0:
            hits.append(
                SearchHit(
                    title=s.name,
                    subtitle=f"{s.code} · {s.category} · {s.city}",
                    url=f"/apps/fornecedores/indicators?id={s.code}",
                    kind=kind,
                    score=sc,
                )
            )
    return hits


class State:
    def __init__(self, sdk: HubSdk, scratch: Path, store: storage.Connection | None, lookup: Lookup) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.store = store
        self.lookup = lookup
        self.templates: dict[str, str] = {}
        self.tasks: set[asyncio.Task[Any]] = set()

    def fragment(self, name: str, req: Request) -> str | None:
        """Fragmento do mockup com o contexto do hub (usuário, idioma, data, nome do app)."""
        body = self.templates.get(name)
        if body is None:
            try:
                body = (HERE / "fragments" / f"{name}.html").read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        return personalize(body, user_of(req), from_headers(req.headers))

    def audit(self, req: Request, action: str, target: str, detail: str) -> None:
        """Auditoria no outbox sem atrasar a resposta (não há transação para compartilhar)."""
        t = asyncio.create_task(self.sdk.emit(user_of(req), action, target, detail))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def user_store(self, req: Request) -> storage.Connection | None:
        if self.store is None:
            return None
        return self.store.for_user(req.headers.get("x-hub-user"), req.headers.get("x-hub-user-sig"))


def user_of(req: Request) -> str:
    return identity_of(req).display_name


def not_found() -> Response:
    return html(
        '<section class="empty-state"><h1>Página não encontrada</h1><p>Esta seção não existe. '
        '<a href="/apps/fornecedores">Voltar ao início do app</a>.</p></section>',
        404,
    )


def with_retry(resp: Response, retry: int | None) -> Response:
    if retry is not None:
        resp.headers["retry-after"] = str(min(retry, 3600))
    return resp


def storage_status(e: storage.StorageError) -> tuple[int, int | None]:
    """Falha do armazenamento → status: transitória 503 + ``Retry-After`` (o hub.js repete
    sozinho); inexistente 404; acima do limite 413; o resto 502."""
    if e.transient:
        return 503, e.retry_after if e.retry_after is not None else 2
    if e.status == 404:
        return 404, None
    if e.status == 413:
        return 413, None
    return 502, None


def shown_name(name: str) -> str:
    """Nome de objeto sem o prefixo de data/hora (``20260926120000-contrato.pdf``)."""
    _, sep, rest = name.partition("-")
    return rest if sep else name


def kb(n: int) -> int:
    return -(-n // 1024)


def info_html(i: CnpjInfo, extra: str) -> str:
    when = esc(i.consultado_em[:19].replace("T", " "))
    return (
        '<div class="app2-metrics hub-metrics">'
        f"<article><span>Score de crédito</span><strong>{i.score}</strong><small>risco {esc(i.risco)}</small></article>"
        f'<article><span>Situação</span><strong class="hub-small">{esc(i.situacao)}</strong><small>{esc(i.razao_social)}</small></article>'
        f'<article><span>Consulta</span><strong class="hub-small">{when}</strong><small>{esc(extra)}</small></article></div>'
    )


def outcome_html(o: Outcome) -> str:
    if o.info is not None:
        return info_html(o.info, f"{o.attempts} tentativa(s) · {o.ms} ms")
    out = notice("warn", "Consulta indisponível no momento", o.reason)
    if o.cached is not None:
        info, age = o.cached
        out += info_html(info, f"último resultado conhecido, de {age} s atrás")
    return out


def lookup_box(sup: Supplier, body: str) -> str:
    return (
        '<section class="app2-panel hub-panel" id="lookup-box"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">CONSULTA CADASTRAL</span><h2>CNPJ {esc(sup.cnpj)}</h2></div>'
        f'<form method="post" action="/apps/fornecedores/suppliers/{esc(sup.code)}/lookup" data-hub-target="#lookup-box">'
        f'<button class="app2-outline" type="submit">Consultar agora</button></form></div>{body}</section>'
    )


def storage_label(st: storage.Connection | None) -> str:
    if st is not None and st.via_broker and st.kind == "sharepoint":
        return f"Guardados no SharePoint (conexão {esc(st.name)})."
    if st is not None and st.via_broker:
        return f"Guardados na conexão {esc(st.name)}."
    return "Guardados no object store do app." if st is not None else ""


async def attachments_html(st: State, sup: Supplier, msg: str = "") -> str:
    conn = st.store
    if conn is None:
        listing = notice(
            "info",
            "Object store não configurado",
            "Defina HUB_S3_ENDPOINT, HUB_S3_BUCKET e as chaves (ou vincule a conexão fornecedores-anexos) para guardar anexos.",
        )
    else:
        try:
            objs = [o for o in await conn.list(sup.code) if not o.folder]
        except storage.StorageError as e:
            objs = []
            listing = (
                '<p class="hub-muted">Nenhum anexo ainda.</p>'
                if e.status == 404
                else notice("error", "Não foi possível listar os anexos", e.message)
            )
        else:
            if not objs:
                listing = '<p class="hub-muted">Nenhum anexo ainda.</p>'
            else:
                rows = "".join(
                    f"<tr><td><b>{esc(shown_name(o.name))}</b></td><td>{thousands(kb(o.size))} KB</td>"
                    f"<td>{esc(o.modified[:16].replace('T', ' '))}</td>"
                    f'<td class="hub-row-actions"><a class="app2-inline-link" download '
                    f'href="{FRAG}/fornecedores/suppliers/{esc(sup.code)}/attachments/{esc(o.name)}">Baixar</a>'
                    f'<form class="hub-inline" method="post" '
                    f'action="/apps/fornecedores/suppliers/{esc(sup.code)}/attachments/{esc(o.name)}/delete" '
                    f'data-hub-target="#attachments"><button type="submit" class="app2-inline-link hub-danger">Excluir</button></form></td></tr>'
                    for o in objs
                )
                listing = (
                    '<div class="app2-table-wrap"><table class="app2-table"><thead><tr><th>ARQUIVO</th>'
                    "<th>TAMANHO</th><th>ENVIADO EM</th><th></th></tr></thead><tbody>"
                    f"{rows}</tbody></table></div>"
                )
    where_kind = "" if st.store is None else (st.store.kind if st.store.via_broker else "s3-app")
    return (
        '<section class="app2-panel hub-panel" id="attachments"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">DOCUMENTOS</span><h2>Anexos</h2><p class="hub-muted" data-storage="{where_kind}">'
        f"{storage_label(st.store)}</p></div></div>{msg}{listing}"
        f'<form class="hub-form hub-upload" method="post" action="/apps/fornecedores/suppliers/{esc(sup.code)}/attachments" '
        'enctype="multipart/form-data" data-hub-target="#attachments"><div class="form-field">'
        f'<label for="f-anexo">Novo anexo (PDF ou imagem, até {attach_max_bytes() // 1024 // 1024} MB)</label>'
        '<input id="f-anexo" type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.webp" required></div>'
        '<div class="modal-actions"><span class="hub-muted" data-hub-progress></span>'
        '<button class="app2-primary" type="submit">Enviar anexo</button></div></form></section>'
    )


async def detail(st: State, sup: Supplier) -> str:
    last = st.lookup.last_good(sup.cnpj)
    body = (
        info_html(last[0], f"consultado há {last[1]} s")
        if last
        else '<p class="hub-muted">Consulte a situação cadastral e o score de crédito na base externa.</p>'
    )
    return (
        '<section class="app2-panel hub-panel hub-supplier"><div class="app2-panel-head"><div>'
        f'<span class="app2-kicker">FICHA DO FORNECEDOR · {esc(sup.code)}</span><h2>{esc(sup.name)}</h2>'
        f'<p class="hub-muted">{esc(sup.category)} · {esc(sup.city)} · CNPJ {esc(sup.cnpj)} · situação: {esc(sup.status)}</p></div>'
        f'<div class="hub-actions"><a class="app2-outline" download href="{FRAG}/fornecedores/suppliers/{esc(sup.code)}/ficha.pdf">Gerar ficha PDF</a>'
        '<a class="app2-outline" href="/apps/fornecedores/indicators">Fechar</a></div></div></section>'
        f"{lookup_box(sup, body)}{await attachments_html(st, sup)}"
    )


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = HubSdk.from_env(APP_ID)
    sdk.search = _search
    st = State(sdk, scratch, attachments_store(), Lookup.from_env(APP_ID))
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.fornecedores = st
    register(app, st)
    # Onda 11: API v1 (OpenAPI em /fornecedores/api/openapi.json; MCP interno gerado dele).
    from fornecedores.api import register_api

    register_api(app, st)
    return app


async def _search(q: SearchQuery) -> list[SearchHit]:
    return search_hits(q)


def register(app: FastAPI, st: State) -> None:
    def static(name: str, req: Request) -> Response:
        body = st.fragment(name, req) if name in SECTIONS or name == "_skeleton" else None
        return html(body) if body is not None else not_found()

    @app.get("/fornecedores")
    @app.get("/fornecedores/")
    async def dashboard(req: Request) -> Response:
        return static("dashboard", req)

    @app.get("/fornecedores/_skeleton")
    async def skeleton(req: Request) -> Response:
        return static("_skeleton", req)

    @app.get("/fornecedores/assets/{name}")
    async def asset(name: str) -> Response:
        p = shared_assets_dir() / "demo.css" if name == "demo.css" else HERE / "assets" / "app.css"
        if name not in ("demo.css", "app.css"):
            return Response(status_code=404)
        try:
            body = p.read_bytes()
        except OSError:
            return Response(status_code=404)
        return Response(body, media_type=CSS, headers={"cache-control": "no-cache"})

    @app.get("/fornecedores/indicators")
    async def directory(req: Request) -> Response:
        page = st.fragment("indicators", req)
        if page is None:
            return not_found()
        # "···" de cada linha vira link para a ficha.
        for s in SUPPLIERS:
            old = (
                f'<button class="app2-more" aria-label="Mais ações" '
                f'data-hub-toast="Detalhes de {esc(s.name)} selecionados.">···</button>'
            )
            new = (
                f'<a class="app2-more" aria-label="Abrir ficha de {esc(s.name)}" '
                f'href="/apps/fornecedores/indicators?id={esc(s.code)}">···</a>'
            )
            page = page.replace(old, new)
        if sup := supplier(param(dict(req.query_params), "id")):
            marker = '<div class="app2-directory-summary">'
            page = page.replace(marker, f"{await detail(st, sup)}{marker}", 1)
        return html(page)

    @app.get("/fornecedores/suppliers/{code}/lookup")
    @app.get("/fornecedores/suppliers/{code}/attachments")
    @app.get("/fornecedores/suppliers/{code}/attachments/{name}/delete")
    async def only_post(code: str) -> Response:
        return not_found()

    @app.post("/fornecedores/suppliers/{code}/lookup")
    async def do_lookup(code: str, req: Request) -> Response:
        sup = supplier(code)
        if sup is None:
            return not_found()
        o = await st.lookup.lookup(sup.cnpj)
        det = (
            f"score {o.info.score} ({o.info.risco}) · {o.attempts} tentativa(s) · {o.ms} ms"
            if o.info is not None
            else f"contingência: {o.reason}"
        )
        st.audit(req, "Consulta cadastral de fornecedor", sup.code, det)
        return html(lookup_box(sup, outcome_html(o)))

    @app.post("/fornecedores/suppliers/{code}/attachments")
    async def upload(code: str, req: Request) -> Response:
        sup = supplier(code)
        if sup is None:
            return not_found()

        async def fail(status: int, title: str, text: str, retry: int | None = None) -> Response:
            return with_retry(
                html(await attachments_html(st, sup, notice("error", title, text)), status), retry
            )

        store = st.user_store(req)
        if store is None:
            return await fail(503, "Object store não configurado", "Não é possível receber anexos agora.")
        limit = attach_max_bytes()
        try:
            declared_len = int(req.headers.get("content-length") or 0)
        except ValueError:
            declared_len = 0
        if declared_len > limit + 1024 * 1024:
            return await fail(
                413, "Arquivo muito grande", f"O limite para anexos é {limit // 1024 // 1024} MB."
            )
        try:
            form = await req.form(max_files=1, max_fields=10)
        except Exception:
            return await fail(400, "Envio interrompido", "O arquivo não pôde ser lido.")
        up = form.get("file")
        if not isinstance(up, UploadFile):
            return await fail(422, "Nenhum arquivo enviado", "Selecione um PDF ou imagem.")
        name = up.filename or "arquivo"
        tmp = st.scratch / f"anexo-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        try:
            ct, size = await _spool(up, tmp, name, limit)
        except ValueError as e:
            return await fail(415, "Arquivo recusado", str(e))
        except OverflowError:
            return await fail(
                413, "Arquivo muito grande", f"O limite para anexos é {limit // 1024 // 1024} MB."
            )
        except OSError as e:
            return await fail(
                503,
                "Espaço de trabalho indisponível",
                f"Nada foi guardado; tente de novo em instantes ({e}).",
                2,
            )
        finally:
            await up.close()
        try:
            key = f"{sup.code}/{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{safe_name(name)}"
            try:
                with tmp.open("rb") as f:
                    await store.upload(key, f, ct, size)
            except storage.StorageError as e:
                status, retry = storage_status(e)
                return await fail(status, "Não foi possível guardar o anexo", e.message, retry)
            except OSError as e:
                return await fail(
                    503,
                    "Espaço de trabalho indisponível",
                    f"Nada foi guardado; tente de novo em instantes ({e}).",
                    2,
                )
        finally:
            tmp.unlink(missing_ok=True)
        st.audit(req, "Anexo de fornecedor enviado", sup.code, f"{name} · {ct} · {size} bytes")
        msg = notice("ok", "Anexo enviado", f"{name} ({kb(size)} KB)")
        return html(await attachments_html(st, sup, msg), 201)

    @app.get("/fornecedores/suppliers/{code}/attachments/{name}")
    async def download(code: str, name: str, req: Request) -> Response:
        sup = supplier(code)
        store = st.user_store(req)
        if sup is None or not valid_object_name(name) or store is None:
            return not_found()
        try:
            d = await store.download(f"{sup.code}/{name}")
        except storage.StorageError as e:
            status, retry = storage_status(e)
            return with_retry(html(notice("error", "Anexo indisponível", e.message), status), retry)
        headers = download_headers(d.content_type, shown_name(name))
        if d.length is not None:
            headers["content-length"] = str(d.length)
        return StreamingResponse(d.chunks(), headers=headers)

    @app.post("/fornecedores/suppliers/{code}/attachments/{name}/delete")
    async def remove(code: str, name: str, req: Request) -> Response:
        sup = supplier(code)
        store = st.user_store(req)
        if sup is None or not valid_object_name(name) or store is None:
            return not_found()
        try:
            await store.delete(f"{sup.code}/{name}")
        except storage.StorageError as e:
            status, retry = storage_status(e)
            body = await attachments_html(st, sup, notice("error", "Não foi possível excluir", e.message))
            return with_retry(html(body, status), retry)
        st.audit(req, "Anexo de fornecedor excluído", sup.code, name)
        return html(await attachments_html(st, sup, notice("ok", "Anexo excluído", name)))

    @app.get("/fornecedores/suppliers/{code}/ficha.pdf")
    async def ficha(code: str, req: Request) -> Response:
        sup = supplier(code)
        if sup is None:
            return not_found()
        permit = st.sdk.heavy.try_acquire()
        if permit is None:
            return busy("A geração do PDF", "PDF em fila…")
        with permit:
            err: str | None = None
            anexos: list[storage.Item] = []
            store = st.user_store(req)
            if store is None:
                err = "Object store não configurado."
            else:
                try:
                    anexos = await store.list(sup.code)
                except storage.StorageError as e:
                    err = None if e.status == 404 else e.message
            data = ficha_pdf(sup, user_of(req), st.lookup.last_good(sup.cnpj), anexos, err)
        st.audit(req, "Ficha de fornecedor gerada (PDF)", sup.code, f"{len(data)} bytes")
        headers = download_headers("application/pdf", f"ficha-{sup.code}.pdf")
        headers["content-length"] = str(len(data))
        return Response(data, headers=headers)

    @app.get("/fornecedores/{section}")
    async def section(section: str, req: Request) -> Response:
        return static(section, req)


async def _spool(up: UploadFile, dest: Path, name: str, limit: int) -> tuple[str, int]:
    """Grava o upload em ``dest`` conferindo tipo (extensão, content-type e assinatura) nos
    primeiros bytes e o limite de tamanho; devolve (content-type, bytes). Em falha apaga ``dest``."""
    size, head, ct = 0, b"", ""
    declared = up.content_type or ""
    out = await asyncio.to_thread(partial(dest.open, "wb"))
    try:
        while chunk := await up.read(1024 * 1024):
            if not ct:
                head += chunk[: max(0, 16 - len(head))]
                if len(head) >= 12:
                    ct = check_type(name, declared, head)
            size += len(chunk)
            if size > limit:
                raise OverflowError
            await asyncio.to_thread(out.write, chunk)
        ct = ct or check_type(name, declared, head)
    except BaseException:
        await asyncio.to_thread(out.close)
        await asyncio.to_thread(partial(dest.unlink, missing_ok=True))
        raise
    await asyncio.to_thread(out.close)
    return ct, size
