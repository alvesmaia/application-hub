"""Coletor — app FastAPI da plataforma (5º app de demonstração, stack Python): bancada de
leitura de códigos para celular/leitor de código de barras, **sem JavaScript próprio**.

Três telas:

- **Escanear** (celular): botão da câmera (leitura contínua), campo do leitor físico sempre
  pronto, alternância "Código de barras" / "QR" (lembrada por usuário), cartão grande do
  último resultado (verde/vermelho + bipe/vibração) e as últimas 10 leituras;
- **Folha de teste** (computador): os códigos para ler (QR "abrir o app" à parte; Itens,
  Locais, Pedidos, Formatos variados) e, ao lado, **Leituras ao vivo** — o que o mesmo
  usuário está lendo no celular, atualizado a cada segundo (``data-hub-trigger="every 1s"``);
- **Diagnóstico**: os recursos do aparelho.

Leitura pela câmera, leitor tipo teclado, bipe/vibração, fila offline e instalação são
**comportamentos do hub** (``data-hub-scan``, ``data-hub-wedge``, ``data-hub-feedback``,
``data-hub-offline-queue``, ``data-hub-install``, ``data-hub-diag`` — CONTRATO-APP §25).

Rotas (sob ``/coletor``; o navegador vê ``/apps/coletor/...``; o hub busca em
``/_frag/coletor/...``): ``GET /coletor`` · ``/escanear`` · ``POST /escanear/ler`` ·
``POST /escanear/prefs`` · ``GET /folha`` · ``GET /folha/leituras`` · ``POST /folha/limpar`` ·
``GET /diagnostico``; telas antigas (``/contagem``, ``/recebimento``, ``/consulta``,
``/insumos``) mostram o Escanear (ou a Folha). API em ``/coletor/api/v1/*``.

Cada leitura grava a linha, o evento de auditoria e a marca de idempotência **na mesma
transação** (a fila offline do hub repete com a mesma ``Idempotency-Key``: uma leitura
repetida nunca entra duas vezes).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import platform
import random
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import asyncpg
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from hub_sdk import __version__ as SDK_VERSION
from hub_sdk import locale as _locale
from hub_sdk import outbox, telemetry
from hub_sdk.app import HubSdk
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import env_of, identity_of
from hub_sdk.locale import t
from hub_sdk.render import ICONS, AppInfo, NavItem, Renderer, html
from hub_sdk.search import SearchHit, SearchQuery, like_pattern, score
from markupsafe import Markup
from starlette.requests import HTTPConnection

from coletor import barcodes, fila, i18n
from coletor.live import LIVE, notify
from coletor.model import (
    SCHEMA_SQL,
    catalog,
    guess_format,
    is_url,
    locations,
    normalize_code,
    orders,
)

log = logging.getLogger("coletor")

APP_ID = "coletor"
HERE = Path(__file__).resolve().parent

ICONS.update(
    {
        "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"></path>'
        '<path d="M7 8v8M10 8v8M13 8v8M16 8v8"></path>',
        "phone": '<rect x="7" y="2" width="10" height="20" rx="2"></rect><path d="M11 18h2"></path>',
        "print": '<path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect>',
        "camera": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"></path><circle cx="12" cy="13" r="4"></circle>',
        "qr": '<rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 17h4v4"></path>',
    }
)

INFO = AppInfo(
    id=APP_ID,
    name="Coletor",
    tagline="Leitura de códigos",
    icon="scan",
    color="#ffc20e",
    ink="#1d2125",
    nav=(
        NavItem("escanear", "Escanear", "scan"),
        NavItem("folha", "Folha de teste", "print"),
        NavItem("diagnostico", "Diagnóstico", "phone"),
    ),
)

ASSETS = {
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "demo.css": ("demo.css", "text/css; charset=utf-8"),
}

# Modos da câmera (alternância do Escanear) → formatos do `data-hub-scan-formats`.
MODES: dict[str, tuple[str, str]] = {
    "barras": ("ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf", "Modo código de barras — troque para QR"),
    "qr": ("qr_code,data_matrix", "Modo QR — troque para Código de barras"),
}
DEFAULT_MODE = "barras"
MODE_KEYS = ("barras", "qr")
MODE_LABELS = {"barras": "Código de barras", "qr": "QR"}
N_SESSION_CODES = 24
SOURCES = {"camera": "câmera", "leitor": "coletor físico", "api": "API"}
FORMAT_LABELS = barcodes.LABELS | {"upc_e": "UPC-E", "itf": "ITF", "pdf417": "PDF417", "codabar": "Codabar"}
MAX_VALUE = 256


def shared_assets_dir() -> Path:
    env = os.environ.get("COLETOR_SHARED_ASSETS", "")
    if env:
        return Path(env)
    return HERE.parents[3] / "demo-apps" / "assets"


def public_url() -> str:
    """Endereço público do hub (QR "abrir no celular" da folha de teste)."""
    return os.environ.get("COLETOR_PUBLIC_URL", "").strip().rstrip("/")


def public_base(req: HTTPConnection) -> str:
    """Base pública do app (``/apps/coletor``) — cabeçalho ``x-hub-base`` do hub."""
    b = req.headers.get("x-hub-base", "").strip()
    ok = b.startswith("/apps/") and all(c.isalnum() or c in "/-" for c in b)
    return b if ok else "/apps/coletor"


async def seed(sdk: HubSdk) -> None:
    """Carga inicial idempotente (catálogo, locais e pedidos — só para nomear leituras)."""
    if sdk.pool is None:
        return
    async with sdk.pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(424250)")
        if int(await c.fetchval("SELECT count(*) FROM col_items")) == 0:
            await c.executemany(
                "INSERT INTO col_items (code, code128, sku, name, unit, category) VALUES ($1,$2,$3,$4,$5,$6) "
                "ON CONFLICT DO NOTHING",
                [(i.code, i.code128, i.sku, i.name, i.unit, i.category) for i in catalog()],
            )
            await c.executemany(
                "INSERT INTO col_locations (code, name, zone) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                [(loc.code, loc.name, loc.zone) for loc in locations()],
            )
            for o in orders():
                await c.execute(
                    "INSERT INTO col_orders (code, supplier, offset_days, expected_date) "
                    "VALUES ($1,$2,$3,current_date + $3::int) ON CONFLICT DO NOTHING",
                    o.code,
                    o.supplier,
                    o.offset_days,
                )
                await c.executemany(
                    "INSERT INTO col_order_lines (order_code, item, expected) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                    [(o.code, item, q) for item, q in o.lines],
                )
            log.warning("coletor: carga inicial (catálogo, locais e pedidos)")
        await c.execute("UPDATE col_orders SET expected_date = current_date + offset_days")


def build_sdk() -> HubSdk:
    async def after(sdk: HubSdk) -> None:
        await seed(sdk)

    return HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, handlers=[], after_migrate=after)


async def run_worker() -> None:
    await build_sdk().run_worker_process()


class State:
    def __init__(self, sdk: HubSdk, render: Renderer) -> None:
        self.sdk = sdk
        self.render = render


def make_search(sdk: HubSdk) -> Any:
    async def search(q: SearchQuery) -> list[SearchHit]:
        if sdk.pool is None or not sdk.db_ready:
            return []
        code = normalize_code(q.q)
        async with sdk.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT code, code128, name, category FROM col_items "
                "WHERE code = $2 OR code128 = $2 OR name ILIKE $1 OR sku ILIKE $1 "
                "ORDER BY (code = $2 OR code128 = $2) DESC, name LIMIT $3",
                like_pattern(q.q),
                code,
                q.limit,
            )
        return [
            SearchHit(
                title=r["name"],
                subtitle=f"{r['code']} · {r['code128']} · {t(r['category'])}",
                url=f"/apps/coletor/escanear?codigo={r['code']}",
                kind=t("Item"),
                score=1.0 if code in (r["code"], r["code128"]) else max(score(r["name"], q.q), 0.3),
            )
            for r in rows
        ]

    return search


def create_app() -> FastAPI:
    sdk = build_sdk()
    sdk.search = make_search(sdk)
    render = Renderer(INFO, HERE / "templates")
    cast(Any, render.env.globals).update(code_svg=barcodes.svg, fmt_label=lambda f: FORMAT_LABELS.get(f, f))
    st = State(sdk, render)
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.state.coletor = st
    register(app, st)
    from coletor.api import register_api

    register_api(app, st)
    return app


# ---- consultas ---------------------------------------------------------------------------
def db_notice(st: State) -> Markup | None:
    if st.sdk.db is None:
        return st.render.notice(
            "info", t("Banco de dados não configurado"), t("Defina HUB_DB_URL para usar o cadastro real.")
        )
    if not st.sdk.db_ready:
        return st.render.notice(
            "warn",
            t("Banco de dados indisponível"),
            t("O banco ainda está subindo ou não respondeu. Tente novamente."),
        )
    return None


def _user(req: HTTPConnection) -> str:
    return identity_of(req).user or "anonimo"


def _name(req: HTTPConnection) -> str:
    return identity_of(req).display_name


async def find_item(c: Any, code: str) -> Any:
    return await c.fetchrow("SELECT * FROM col_items WHERE code = $1 OR code128 = $1", code)


def _clean(raw: str) -> str:
    return "".join(ch for ch in str(raw) if ch.isprintable()).strip()[:MAX_VALUE]


def _link_label(value: str) -> str:
    parts = urlsplit(value.strip())
    own = "coletor" in parts.path or parts.netloc.endswith(urlsplit(public_url()).netloc or "\x00")
    return t("Link para o app — abre o Coletor") if own else t("Link (endereço web), não um código")


async def classify(c: Any, raw: str) -> tuple[str, str]:
    """(tipo, rótulo) de uma leitura: item, local, pedido, link ou desconhecido."""
    if is_url(raw):
        return "link", _link_label(raw)
    code = normalize_code(raw)
    if not code:
        return "desconhecido", t("Código desconhecido")
    if c is not None:
        item = await find_item(c, code)
        if item is not None:
            return "item", t("Item: {n}", n=item["name"])
        loc = await c.fetchrow("SELECT code, name FROM col_locations WHERE code = $1", code)
        if loc is not None:
            return "local", t("Local: {c} · {n}", c=loc["code"], n=loc["name"])
        o = await c.fetchrow("SELECT code, supplier FROM col_orders WHERE code = $1", code)
        if o is not None:
            return "pedido", t("Pedido {c} · {s}", c=o["code"], s=o["supplier"])
    return "desconhecido", t("Código desconhecido")


def feedback_of(kind: str) -> str:
    return {"item": "ok", "local": "ok", "pedido": "ok", "link": "warn"}.get(kind, "error")


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["feedback"] = feedback_of(d["kind"])
    d["format_label"] = FORMAT_LABELS.get(d["format"], d["format"])
    d["source_label"] = t(str(SOURCES.get(d["source"]) or d["source"]))
    at = d.get("at")
    d["time"] = at.astimezone().strftime("%H:%M:%S") if isinstance(at, dt.datetime) else ""
    return d


async def recent_reads(c: Any, user: str, limit: int) -> list[dict[str, Any]]:
    rows = await c.fetch(
        "SELECT id, value, format, source, kind, label, at FROM col_reads WHERE user_name = $1 "
        "ORDER BY id DESC LIMIT $2",
        user,
        limit,
    )
    return [_row(r) for r in rows]


async def store_read(c: Any, req: Request, value: str, fmt: str, source: str) -> dict[str, Any]:
    kind, label = await classify(c, value)
    row = await c.fetchrow(
        "INSERT INTO col_reads (user_name, value, format, source, kind, label) VALUES ($1,$2,$3,$4,$5,$6) "
        "RETURNING id, value, format, source, kind, label, at",
        _user(req),
        value,
        fmt,
        source,
        kind,
        label,
    )
    await outbox.write(c, outbox.audit(_name(req), "Leitura registrada", value[:64], f"{fmt} · {label}"))
    await notify(c, _user(req), "read")
    return _row(row)


async def scan_mode(c: Any, user: str) -> str:
    m = await c.fetchval("SELECT scan_mode FROM col_prefs WHERE user_name = $1", user) if c else None
    return m if isinstance(m, str) and m in MODES else DEFAULT_MODE


async def load_reader(c: Any, user: str, lock: bool = False) -> fila.Reader:
    row = await c.fetchrow(
        "SELECT reader_mode, cooldown_ms, hold, last_value, last_at FROM col_prefs WHERE user_name = $1"  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        + (" FOR UPDATE" if lock else ""),
        user,
    )
    if row is None:
        return fila.Reader()
    return fila.Reader(
        mode=fila.clean_reader_mode(row["reader_mode"]),
        cooldown_ms=fila.clean_cooldown(row["cooldown_ms"]),
        hold=bool(row["hold"]),
        last_value=row["last_value"],
        last_at=float(row["last_at"]),
    )


async def save_reader(c: Any, user: str, rd: fila.Reader) -> None:
    await c.execute(
        "INSERT INTO col_prefs (user_name, reader_mode, cooldown_ms, hold, last_value, last_at) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_name) DO UPDATE SET reader_mode = $2, "
        "cooldown_ms = $3, hold = $4, last_value = $5, last_at = $6",
        user,
        rd.mode,
        rd.cooldown_ms,
        rd.hold,
        rd.last_value,
        rd.last_at,
    )


def mode_ctx(mode: str, rd: fila.Reader | None = None) -> dict[str, Any]:
    formats, wrong = MODES[mode]
    rd = rd or fila.Reader()
    return {
        "mode": mode,
        "formats": formats,
        "wrong_hint": t(wrong),
        "mode_formats": "|".join(MODES[k][0] for k in MODE_KEYS),
        "mode_hints": "|".join(t(MODES[k][1]).replace("|", "/") for k in MODE_KEYS),
        "mode_labels": "|".join(t(MODE_LABELS[k]) for k in MODE_KEYS),
        "mode_keys": "|".join(MODE_KEYS),
        "reader": rd,
        "cooldowns": fila.COOLDOWNS_MS,
        "dedup_ms": fila.DEDUP_MS,
    }


# ---- sessão de teste (fila) -----------------------------------------------------------------
def session_codes() -> list[fila.Code]:
    """Os 24 códigos da sessão (a ordem é sorteada a cada início/reinício)."""
    items = catalog()
    locs = locations()
    ords = orders()
    out: list[tuple[str, str, str]] = []
    out += [("ean_13", it.code[:12], t("Item: {n}", n=it.name)) for it in items[:8]]
    out += [("code_39", loc.code, t("Local: {c} · {n}", c=loc.code, n=loc.name)) for loc in locs[:6]]
    out += [("qr_code", o.code, t("Pedido {c} · {s}", c=o.code, s=o.supplier)) for o in ords]
    out += [("code_128", it.code128, t("Item: {n}", n=it.name)) for it in items[3:6]]
    out += [
        ("ean_8", "9638507", t("Formato EAN-8")),
        ("upc_a", "03600029145", t("Formato UPC-A")),
        ("code_128", items[1].code128, t("Item: {n}", n=items[1].name)),
        ("data_matrix", items[2].code128, t("Item: {n}", n=items[2].name)),
    ]
    codes = []
    for fmt, value, label in out:
        full = barcodes.encode(fmt, value).text
        codes.append(fila.Code(fmt=fmt, value=full, expected=fila.match_key(full), label=label))
    return codes


_SEEDED: random.Random | None = None


def rng() -> random.Random:
    """Sorteio da ordem: ``COLETOR_SHUFFLE_SEED`` fixa a semente do processo (testes: a
    sequência de sorteios é reproduzível e cada início/reinício sorteia de novo); sem ela,
    aleatório."""
    global _SEEDED
    seed = os.environ.get("COLETOR_SHUFFLE_SEED", "").strip()
    if seed.isdigit():
        if _SEEDED is None:
            _SEEDED = random.Random(int(seed))  # noqa: S311 — ordem de teste
        return _SEEDED
    return random.Random(random.SystemRandom().randrange(1 << 62))  # noqa: S311


async def load_session(c: Any, user: str, lock: bool = False) -> fila.Session | None:
    query = "SELECT state FROM col_sessions WHERE user_name = $1" + (" FOR UPDATE" if lock else "")  # noqa: S608 -- lock is a fixed SQL fragment; user data is bound
    raw = await c.fetchval(query, user)
    if raw is None:
        return None
    try:
        return fila.Session.from_json(json.loads(raw) if isinstance(raw, str) else raw)
    except (ValueError, TypeError, KeyError):
        return None


async def save_session(c: Any, user: str, s: fila.Session) -> None:
    await c.execute(
        "INSERT INTO col_sessions (user_name, state, updated_at) VALUES ($1, $2::jsonb, now()) "
        "ON CONFLICT (user_name) DO UPDATE SET state = $2::jsonb, updated_at = now()",
        user,
        json.dumps(s.to_json()),
    )


def session_view(s: fila.Session | None) -> dict[str, Any]:
    """Contexto das telas da sessão (folha e celular)."""
    if s is None:
        return {"sess": None}
    cur = s.current()
    res = []
    for r in reversed(s.results[-40:]):
        res.append(r | {"fmt_label": FORMAT_LABELS.get(r.get("fmt", ""), r.get("fmt", ""))})
    sm = fila.summary(s)
    return {
        "sess": s,
        "cur": cur,
        "cur_fmt": FORMAT_LABELS.get(cur.fmt, cur.fmt) if cur else "",
        "shown": min(s.pos + 1, s.total),
        "results": res,
        "summary": sm,
        "avg_s": f"{sm.avg_ms / 1000:.1f}".replace(".", ","),
        "total_s": f"{sm.total_ms / 1000:.0f}",
        "pct": round(100 * min(s.pos, s.total) / s.total) if s.total else 0,
    }


def read_format(form: dict[str, str], value: str) -> tuple[str, str]:
    """(formato, origem): com formato vindo da câmera (hub) → câmera; sem → leitor físico."""
    f = form.get("formato", "").strip().lower()
    if f and f.replace("_", "").isalnum() and len(f) <= 16:
        return f, "camera"
    return guess_format(value), "leitor"


# ---- rotas ---------------------------------------------------------------------------
def register(app: FastAPI, st: State) -> None:
    r = st.render

    @app.get("/coletor/_skeleton")
    async def skeleton() -> Response:
        return r.skeleton()

    @app.get("/coletor/assets/{name}")
    async def asset(name: str) -> Response:
        a = ASSETS.get(name)
        if a is None:
            return Response(status_code=404)
        file, mime = a
        base = shared_assets_dir() if name == "demo.css" else HERE / "assets"
        try:
            body = await asyncio.to_thread((base / file).read_bytes)
        except OSError:
            return Response(status_code=404)
        return Response(body, media_type=mime, headers={"cache-control": "no-cache"})

    # ---- escanear ----------------------------------------------------------------------------
    async def scan_ctx(c: Any, req: HTTPConnection) -> dict[str, Any]:
        user = _user(req)
        rd = await load_reader(c, user)
        ctx = mode_ctx(await scan_mode(c, user), rd)
        ctx |= session_view(await load_session(c, user))
        return ctx

    @app.get("/coletor")
    @app.get("/coletor/")
    @app.get("/coletor/dashboard")
    @app.get("/coletor/escanear")
    @app.get("/coletor/contagem")
    @app.get("/coletor/recebimento")
    @app.get("/coletor/consulta")
    async def escanear(req: Request) -> Response:
        n = db_notice(st)
        ctx: dict[str, Any] = {
            "notice_html": n or "",
            "reads": [],
            "last": None,
            "lookup": None,
            "sess": None,
        }
        ctx |= mode_ctx(DEFAULT_MODE)
        if n is None:
            try:
                async with st.sdk.pool.acquire() as c, telemetry.db("SELECT col_reads"):
                    ctx |= await scan_ctx(c, req)
                    ctx["reads"] = await recent_reads(c, _user(req), 10)
                    ctx["last"] = ctx["reads"][0] if ctx["reads"] else None
                    want = _clean(req.query_params.get("codigo", ""))
                    if want:
                        kind, label = await classify(c, want)
                        ctx["lookup"] = {
                            "value": want,
                            "kind": kind,
                            "label": label,
                            "feedback": feedback_of(kind),
                        }
            except (OSError, asyncpg.PostgresError) as e:
                ctx["notice_html"] = r.notice("error", t("Erro ao consultar as leituras"), str(e))
        return r.page(req, "escanear.html", "escanear", **ctx)

    @app.post("/coletor/escanear/ler")
    async def escanear_ler(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        form = {k: str(v) for k, v in (await req.form()).items()}
        value = _clean(form.get("codigo", ""))
        user = _user(req)
        if not value:
            async with st.sdk.pool.acquire() as c:
                reads = await recent_reads(c, user, 10)
                rd = await load_reader(c, user)
            return r.fragment("scan_result.html", 422, last=None, reads=reads, empty=True, reader=rd)
        fmt, source = read_format(form, value)
        now = time.time()
        outcome: str | None = None
        gate = "aceita"
        last: dict[str, Any] | None = None
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                rd = await load_reader(c, user, lock=True)
                gate = fila.reader_gate(rd, value, now)
                if gate == "aceita":
                    await save_reader(c, user, rd)
                    last = await store_read(c, req, value, fmt, source)
                    sess = await load_session(c, user, lock=True)
                    if sess is not None and sess.active:
                        outcome = fila.on_read(sess, value, now)
                        if outcome == "ignorado":
                            outcome = None
                        if outcome is not None:
                            await save_session(c, user, sess)
                            await notify(c, user, "session")
                    if outcome == fila.ERRO:
                        last["feedback"] = "error"
                if (ctx := idem_ctx(req)) is not None:
                    await ctx.mark_committed(c)
            async with st.sdk.pool.acquire() as c:
                reads = await recent_reads(c, user, 10)
        except (OSError, asyncpg.PostgresError) as e:
            return html(str(r.notice("error", t("Erro ao registrar a leitura"), str(e))), 503)
        if last is None:
            # Recusada pelo modo do leitor (esperando "Próximo", pausa ou repetida).
            prev = reads[0] if reads else None
            return r.fragment(
                "scan_result.html", last=prev, reads=reads, empty=False, gate=gate, reader=rd, fresh=False
            )
        return r.fragment(
            "scan_result.html", last=last, reads=reads, empty=False, gate=gate, reader=rd, outcome=outcome
        )

    @app.post("/coletor/escanear/proximo")
    async def escanear_proximo(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        user = _user(req)
        async with st.sdk.pool.acquire() as c, c.transaction():
            rd = await load_reader(c, user, lock=True)
            fila.reader_next(rd)
            await save_reader(c, user, rd)
            if (ctx := idem_ctx(req)) is not None:
                await ctx.mark_committed(c)
            reads = await recent_reads(c, user, 10)
        return r.fragment(
            "scan_result.html",
            last=reads[0] if reads else None,
            reads=reads,
            empty=False,
            reader=rd,
            fresh=False,
        )

    @app.post("/coletor/escanear/prefs")
    @app.post("/coletor/escanear/modo")
    @app.post("/coletor/escanear/leitor")
    async def escanear_prefs(req: Request) -> Response:
        """Escolhas feitas DENTRO da câmera (barra de cima da camada do hub, `data-hub-scan-prefs`):
        tipo de código (`modo`), modo do leitor (`leitor`) e pausa (`pausa`), por usuário. Campos
        ausentes ficam como estão. `/modo` e `/leitor` são os nomes antigos (mesma rota)."""
        form = {k: str(v) for k, v in (await req.form()).items()}
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        user = _user(req)
        async with st.sdk.pool.acquire() as c, c.transaction():
            if "modo" in form:
                mode = form["modo"] if form["modo"] in MODES else DEFAULT_MODE
                await c.execute(
                    "INSERT INTO col_prefs (user_name, scan_mode) VALUES ($1, $2) "
                    "ON CONFLICT (user_name) DO UPDATE SET scan_mode = $2",
                    user,
                    mode,
                )
            rd = await load_reader(c, user, lock=True)
            if "leitor" in form:
                new = fila.clean_reader_mode(form["leitor"])
                if new != rd.mode:
                    rd.mode = new
                    rd.hold, rd.last_value, rd.last_at = False, "", 0.0
            if "pausa" in form:
                rd.cooldown_ms = fila.clean_cooldown(form["pausa"])
            await save_reader(c, user, rd)
            if (ctx := idem_ctx(req)) is not None:
                await ctx.mark_committed(c)
        return Response(status_code=204)

    @app.get("/coletor/escanear/sessao")
    async def escanear_sessao(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        async with st.sdk.pool.acquire() as c:
            v = session_view(await load_session(c, _user(req)))
        return r.fragment("session_phone.html", **v)

    # ---- folha de teste: sessão em fila + leituras ao vivo -------------------------------------
    def sheet_ctx(req: HTTPConnection) -> dict[str, Any]:
        items = catalog()[:8]
        ords = orders()
        locs = locations()
        samples = [
            ("ean_13", items[0].code),
            ("ean_8", "9638507"),
            ("upc_a", "03600029145"),
            ("code_128", items[1].code128),
            ("code_39", locs[0].code),
            ("qr_code", ords[0].code),
            ("data_matrix", items[2].code128),
        ]
        return {
            "samples": samples,
            "items": items,
            "order": ords[0],
            "locs": locs,
            "app_url": (public_url() + public_base(req)) if public_url() else "",
            "today": dt.date.today(),
            "n_codes": N_SESSION_CODES,
        }

    async def folha_state_ctx(c: Any, req: HTTPConnection) -> dict[str, Any]:
        user = _user(req)
        ctx = sheet_ctx(req)
        ctx |= session_view(await load_session(c, user))
        ctx["reads"] = await recent_reads(c, user, 30)
        ctx["total"] = int(await c.fetchval("SELECT count(*) FROM col_reads WHERE user_name = $1", user))
        return ctx

    async def _live(req: Request, status: int = 200) -> Response:
        async with st.sdk.pool.acquire() as c:
            reads = await recent_reads(c, _user(req), 30)
            total = int(await c.fetchval("SELECT count(*) FROM col_reads WHERE user_name = $1", _user(req)))
        return r.fragment("live_reads.html", status, reads=reads, total=total)

    @app.get("/coletor/folha")
    @app.get("/coletor/insumos")
    async def folha(req: Request) -> Response:
        n = db_notice(st)
        ctx: dict[str, Any] = sheet_ctx(req) | {"sess": None, "reads": [], "total": 0}
        if n is None:
            try:
                async with st.sdk.pool.acquire() as c:
                    ctx |= await folha_state_ctx(c, req)
            except (OSError, asyncpg.PostgresError) as e:
                n = r.notice("error", t("Erro ao consultar as leituras"), str(e))
        return r.page(req, "folha.html", "folha", notice_html=n or "", db_ok=n is None, **ctx)

    @app.get("/coletor/folha/estado")
    async def folha_estado(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        async with st.sdk.pool.acquire() as c:
            ctx = await folha_state_ctx(c, req)
        return r.fragment("folha_state.html", db_ok=True, **ctx)

    @app.get("/coletor/folha/leituras")
    async def folha_leituras(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        return await _live(req)

    @app.post("/coletor/folha/limpar")
    async def folha_limpar(req: Request) -> Response:
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        async with st.sdk.pool.acquire() as c, c.transaction():
            await c.execute("DELETE FROM col_reads WHERE user_name = $1", _user(req))
            await outbox.write(c, outbox.audit(_name(req), "Leituras limpas", _user(req), ""))
            await notify(c, _user(req), "read")
            if (ctx := idem_ctx(req)) is not None:
                await ctx.mark_committed(c)
        return await _live(req)

    actions = ("iniciar", "proximo", "parar", "continuar", "cancelar", "reiniciar", "auto")

    @app.post("/coletor/sessao/{acao}")
    async def sessao(acao: str, req: Request) -> Response:
        """Controles da sessão (computador e celular): o estado é do servidor, um por
        usuário; os dois lados recebem a mudança pelo WebSocket."""
        if acao not in actions:
            return r.not_found()
        if (n := db_notice(st)) is not None:
            return html(str(n), 503)
        form = {k: str(v) for k, v in (await req.form()).items()}
        tela = "escanear" if form.get("tela") == "escanear" else "folha"
        user = _user(req)
        now = time.time()
        async with st.sdk.pool.acquire() as c, c.transaction():
            s = await load_session(c, user, lock=True)
            changed = True
            if acao == "iniciar":
                if s is None or not s.active:
                    s = fila.start(session_codes(), rng(), now, auto=form.get("auto") == "1")
                    await outbox.write(
                        c, outbox.audit(_name(req), "Sessão de teste iniciada", s.id, str(s.total))
                    )
                else:
                    changed = False
            elif s is None:
                changed = False
            elif acao == "proximo":
                changed = fila.next_code(s, now)
            elif acao == "parar":
                changed = fila.pause(s, now)
            elif acao == "continuar":
                changed = fila.resume(s, now)
            elif acao == "cancelar":
                changed = fila.cancel(s, now)
            elif acao == "reiniciar":
                s = fila.restart(s, rng(), now)
                await outbox.write(
                    c, outbox.audit(_name(req), "Sessão de teste reiniciada", s.id, str(s.total))
                )
            else:  # "auto"
                fila.set_auto(s, form.get("auto") == "1")
            if s is not None and changed:
                await save_session(c, user, s)
                await notify(c, user, "session")
            if (ctx := idem_ctx(req)) is not None:
                await ctx.mark_committed(c)
            if tela == "escanear":
                return r.fragment("session_phone.html", **session_view(s))
            return r.fragment("folha_state.html", db_ok=True, **await folha_state_ctx(c, req))

    # ---- tempo real ----------------------------------------------------------------------------
    async def ws_payload(ws: WebSocket, view: str) -> str:
        async with st.sdk.pool.acquire() as c:
            if view == "escanear":
                frag = r.render("session_phone.html", **session_view(await load_session(c, _user(ws))))
                target = "#col-session"
            else:
                frag = r.render("folha_state.html", db_ok=True, **await folha_state_ctx(c, ws))
                target = "#col-folha-state"
        return json.dumps({"target": target, "swap": "outerHTML", "html": frag})

    @app.websocket("/coletor/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        """Estado ao vivo do usuário: a cada leitura/mudança da sessão, o fragmento da tela
        (``view=folha`` ou ``escanear``) é redesenhado e enviado (hub-kit ``data-hub-ws``)."""
        view = "escanear" if ws.query_params.get("view") == "escanear" else "folha"
        user = _user(ws)
        if user == "anonimo":
            await ws.close(code=1008)
            return
        if db_notice(st) is not None:
            await ws.close(code=1013)
            return
        _locale.use(_locale.from_headers(ws.headers))  # a tarefa do socket tem o próprio contexto
        await ws.accept()
        await LIVE.ensure_listener(getattr(st.sdk.db, "url", None))
        q = LIVE.subscribe(user)

        async def reader() -> None:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                while True:
                    await ws.receive_text()

        rx = asyncio.create_task(reader())
        try:
            await ws.send_text(await ws_payload(ws, view))
            while not rx.done():
                get = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({get, rx}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                while not q.empty():  # avisos em rajada → um redesenho
                    q.get_nowait()
                await ws.send_text(await ws_payload(ws, view))
        except (WebSocketDisconnect, RuntimeError, OSError, asyncpg.PostgresError):
            pass
        finally:
            rx.cancel()
            LIVE.unsubscribe(user, q)
            with contextlib.suppress(RuntimeError):
                await ws.close()

    # ---- diagnóstico -------------------------------------------------------------------------
    @app.get("/coletor/diagnostico")
    async def diagnostico(req: Request) -> Response:
        return r.page(
            req,
            "diagnostico.html",
            "diagnostico",
            py=platform.python_version(),
            sdk=SDK_VERSION,
            version=st.sdk.version,
            env=env_of(req),
            db_ready=st.sdk.db_ready,
        )

    @app.get("/coletor/{section}")
    async def other(section: str) -> Response:
        return r.not_found()


_ = i18n  # o catálogo de traduções se registra ao importar
