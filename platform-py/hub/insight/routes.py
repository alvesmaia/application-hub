"""Páginas e APIs de Logs, Auditoria, Saúde/SLOs, Feature flags, Observabilidade e Analytics (`/admin/*`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from hub.admin.config import PERMISSIONS, AdminError, env_label
from hub.admin.routes import dumps, json_only, relative
from hub.admin.store import AdminStore
from hub.insight.fmt import num, pc2
from hub.insight.metrics import enabled, slo_status
from hub.insight.runtime import Insight
from hub.insight.usage import PLATFORM
from hub.ops import updates
from hub.ux.pager import paginate
from hub.ux.texts import page_response


def ms_text(us: int) -> str:
    m = us / 1000
    return f"{num(m / 1000, 2)} s" if m >= 1000 else f"{num(m, 2 if m < 10 else 0)} ms"


def split_ids(s: str) -> list[tuple[str, bool]]:
    """Separa identificadores técnicos (`hubt_…`, `HUB_*`) do texto, para exibi-los como código."""
    out: list[tuple[str, bool]] = []
    rest = s
    while True:
        found = [i for i in (rest.find("hubt_"),) if i >= 0]
        for i in range(len(rest) - 4):
            nxt = rest[i + 4 : i + 5]
            if (
                rest.startswith("HUB_", i)
                and nxt.isascii()
                and nxt.isupper()
                and not (i and ((rest[i - 1].isascii() and rest[i - 1].isalnum()) or rest[i - 1] == "_"))
            ):
                found.append(i)
                break
        if not found:
            break
        i = min(found)
        if i:
            out.append((rest[:i], False))
        tail = rest[i:]
        end = next(
            (j for j, c in enumerate(tail) if not ((c.isascii() and c.isalnum()) or c in "_…")), len(tail)
        )
        out.append((tail[:end], True))
        rest = tail[end:]
    if rest:
        out.append((rest, False))
    return out


def chart(title: str, unit: str, series: list[tuple[str, str, list[float | None]]]) -> dict[str, Any]:
    mx = max((v for s in series for v in s[2] if v is not None), default=0.0)
    mx = max(mx, 0.0)
    top = 1.0 if mx <= 0 else mx * 1.15
    n = max(len(series[0][2]) if series else 0, 2)
    lines = [
        (
            " ".join(
                f"{i / (n - 1) * 600:.1f},{140 - (v or 0.0) / top * 140:.1f}" for i, v in enumerate(pts)
            ),
            f"stroke:{color}",
            label,
        )
        for label, color, pts in series
    ]
    return {
        "title": title,
        "subtitle": f"máx. {num(mx, 2 if mx < 10 else 0)} {unit}",
        "lines": lines,
        "ymax": num(top, 2 if top < 10 else 0),
        "ymid": num(top / 2, 2 if top < 20 else 0),
        "empty": mx <= 0,
    }


def mount_insight(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    insight: Insight,
    client: httpx.AsyncClient,
) -> None:
    api = APIRouter(prefix="/api/admin", dependencies=[Depends(json_only)])
    store = cast(AdminStore, app.state.admin)
    ops, tel, an = insight.ops, insight.tel, insight.analytics

    def refs() -> list[dict[str, str]]:
        return cast(list[dict[str, str]], app.state.app_refs())

    def short(app_id: str) -> str:
        return next((a["short"] for a in refs() if a["id"] == app_id), "Plataforma")

    def meta_json() -> str:
        apps = [{k: a[k] for k in ("color", "id", "ink", "name", "short")} for a in refs()]
        return dumps({"apps": apps, "permissions": list(PERMISSIONS)})

    def app_ids_meta() -> dict[str, Any]:
        return {"apps": [{"id": a["id"], "short": a["short"]} for a in refs()]}

    def render(
        request: Request, name: str, eyebrow: str, title: str, subtitle: str, tab: str = "", **ctx: Any
    ) -> Response:
        data = page_ctx(request) | {
            "icon": icon,
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
            "tab": tab or title,
        }
        return page_response(templates, request, name, data | ctx)

    def actor_of(request: Request) -> str:
        return str(request.state.user["name"])

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as e:
            raise AdminError("Dados inválidos: verifique os campos enviados.") from e
        if not isinstance(data, dict):
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        return data

    def can_edit(request: Request) -> bool:
        return "editar_configuracoes" in request.state.permissions

    # ---- Logs ------------------------------------------------------------------------------------------
    @app.get("/admin/logs", response_class=HTMLResponse)
    async def logs_page(request: Request) -> Response:
        lines = [
            {
                "time": x["time"],
                "level": x["level"],
                "css": {"ERROR": "log-level log-level-error", "WARN": "log-level log-level-warn"}.get(
                    x["level"], "log-level log-level-info"
                ),
                "app": short(x["app"]),
                "user": f"@{x['user']}" if x["user"] else "",
                "message": x["message"],
            }
            for x in store.logs.query({"minutes": "60"})
        ]
        apps = [("all", "Todas as aplicações"), ("platform", "Plataforma")] + [
            (a["id"], a["name"]) for a in refs()
        ]
        levels = [
            ("", "Todos"),
            ("ERROR", "Erro"),
            ("WARN", "Aviso"),
            ("WARN,ERROR", "Aviso ou erro"),
            ("INFO", "Informação"),
        ]
        sources = [
            ("", "Todas"),
            ("gateway", "Requisições"),
            ("container", "Saída dos contêineres"),
            ("app", "Eventos dos apps"),
            ("auth", "Acesso"),
            ("admin", "Administração"),
            ("ops", "Operações"),
        ]
        ranges = [
            ("60", "Última hora"),
            ("1440", "Últimas 24 horas"),
            ("10080", "Últimos 7 dias"),
            ("43200", "Últimos 30 dias"),
        ]
        return render(
            request,
            "admin_logs.html",
            "Operação",
            "Logs da plataforma",
            "Registros reais: requisições, acessos, operações e a saída dos contêineres dos apps. Filtre ou acompanhe em tempo real.",
            tab="Logs",
            lines=lines,
            apps=apps,
            levels=levels,
            sources=sources,
            ranges=ranges,
            meta=meta_json(),
        )

    @api.get("/logs")
    async def list_logs(request: Request) -> Any:
        return store.logs.query(dict(request.query_params))

    @api.get("/logs/stats")
    async def log_stats() -> Any:
        return {"subscribers": len(store.logs.subscribers)}

    @api.get("/logs/stream")
    async def stream_logs(request: Request) -> Response:
        f = {k: v for k, v in request.query_params.items() if k != "minutes"}

        async def events() -> AsyncIterator[str]:
            yield "event: ready\ndata: conectado\n\n"
            feed = store.logs.subscribe()
            nxt = asyncio.ensure_future(anext(feed))
            try:
                while True:
                    done, _ = await asyncio.wait({nxt}, timeout=15)
                    if not done:
                        yield ": keep-alive\n\n"
                        continue
                    line = nxt.result()
                    nxt = asyncio.ensure_future(anext(feed))
                    if store.logs.matches(line, f, int(time.time() * 1000)):
                        yield f"event: log\ndata: {json.dumps(line, ensure_ascii=False, separators=(',', ':'))}\n\n"
            finally:
                nxt.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await nxt
                await feed.aclose()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    # ---- Auditoria -------------------------------------------------------------------------------------
    async def trail() -> list[dict[str, Any]]:
        return list(reversed((await store.cfg.read()).get("audit", [])))

    def usage_of(request: Request) -> dict[str, Any]:
        """Cartão "Uso da plataforma (30 dias)" (o mesmo das Configurações)."""
        sm = an.summary(int(time.time() * 1000))
        names = {a["id"]: a["short"] for a in refs()}
        top = sorted(((names.get(r[0], r[0]), r[3], r[4]) for r in sm["apps"]), key=lambda x: -x[2])[:5]
        return {"mau": sm["mau"], "apps": top, "zero": sm["searches_zero"], "searches": sm["searches"]}

    @app.get("/admin/audit", response_class=HTMLResponse)
    async def audit_page(request: Request, scope: str = "") -> Response:
        perms = getattr(request.state, "permissions", [])
        ids = {a["id"] for a in refs()}
        flt = scope if scope == "platform" or scope in ids else ""
        chips = [("", "Todas", not flt), ("platform", "Plataforma", flt == "platform")]
        chips += [(a["id"], a["short"], flt == a["id"]) for a in refs()]
        now = int(time.time() * 1000)
        rows = [
            {
                "icon": e["icon"],
                "title": e["title"],
                "detail": split_ids(f"{e['actor']} · {e['detail']} · {short(e['scope'])}"),
                "time": relative(e["at"], now),
            }
            for e in [e for e in await trail() if not flt or e["scope"] == flt][:100]
        ]
        chain = request.app.state.admin.chain
        head_file = chain.head_file()
        published = (
            ("Proteção contra rollback: âncora externa", f"Cabeça assinada publicada em {head_file}.", False)
            if head_file and not chain.head_error
            else ("Proteção contra rollback: falha na âncora externa", chain.head_error, True)
            if head_file
            else None
        ) or (
            "Proteção contra rollback: só local",
            "Sem publicação externa do head da auditoria, um rollback do diretório de dados inteiro não é detectado. "
            "Para ativar, defina HUB_AUDIT_HEAD_URL ou HUB_AUDIT_HEAD_FILE (FAQ 16).",
            False,
        )
        rows, pager = paginate(request, rows)
        return render(
            request,
            "admin_audit.html",
            "Governança",
            "Auditoria e atividade",
            "Eventos administrativos recentes, filtrados por aplicação. A trilha é encadeada por hashes e assinada: "
            "qualquer edição, remoção ou reordenação é detectada.",
            tab="Auditoria",
            chips=chips,
            rows=rows,
            blocked=store.chain.blocked,
            published=published,
            meta=meta_json(),
            pager=pager,
            usage=None if "editar_configuracoes" in perms else usage_of(request),
        )

    @api.get("/audit")
    async def list_audit(scope: str = "", limit: int = 100) -> Any:
        limit = min(max(limit, 1), 5000)
        return [e for e in await trail() if scope in ("", "all") or e["scope"] == scope][:limit]

    @api.post("/audit/verify")
    async def verify_audit(request: Request) -> Any:
        report = await asyncio.to_thread(store.chain.report)
        detail = (
            f"Íntegra: {report['entries']} eventos, {report['checkpoints']} checkpoints"
            if report["ok"]
            else f"{len(report['problems'])} problema(s): {report['problems'][0]}"
        )
        with contextlib.suppress(
            RuntimeError
        ):  # cadeia bloqueada: não dá para registrar (o relatório já diz por quê)
            await store.audit(actor_of(request), "Integridade da auditoria verificada", detail, icon="check")
        return report

    @api.get("/audit/export")
    async def export_audit(request: Request) -> Response:
        text = await asyncio.to_thread(store.chain.export)
        await store.audit(actor_of(request), "Auditoria exportada (JSONL com provas)", "", icon="file")
        return Response(
            text,
            media_type="application/x-ndjson",
            headers={"content-disposition": "attachment; filename=auditoria.jsonl"},
        )

    # ---- Saúde, SLOs e incidentes ----------------------------------------------------------------------
    @app.get("/admin/health", response_class=HTMLResponse)
    async def health_page(
        request: Request,
        app_q: str = Query("", alias="app"),
        env: str = "",
        win: str = Query("", alias="min"),
    ) -> Response:
        data = await ops.read()
        now = int(time.time() * 1000)
        slos = []
        for s in data.get("slos", []):
            x = slo_status(tel.metrics, s)
            b = max(-1.0, min(1.0, x["budget_remaining"]))
            color = (
                "var(--color-success)"
                if b > 0.25
                else "var(--color-warning)"
                if b > 0
                else "var(--color-danger)"
            )
            slos.append(
                {
                    "app": short(s["app"]),
                    "env": env_label(s["env"]),
                    "target": f"{s['availability']:g}% · {s['latency_target']:g}% ≤ {s['latency_ms']} ms · {s['window_days']} dias".replace(
                        ".", ","
                    ),
                    "sli": f"{pc2(x['availability'])} ({x['requests']} req.)",
                    "latency": pc2(x["latency"]) if x["latency"] is not None else "—",
                    "budget": pc2(max(x["budget_remaining"], -9.99)),
                    "budget_style": f"width:{max(b, 0.0) * 100:.0f}%;background:{color}",
                    "burn": f"{x['burn_1h']:.1f}× (1 h) · {x['burn_5m']:.1f}× (5 min)".replace(".", ","),
                    "css": "status-pill status-active" if x["met"] else "status-pill status-maintenance",
                    "state": "Dentro da meta" if x["met"] else "Fora da meta",
                    "item": json.dumps(s, ensure_ascii=False, separators=(",", ":")),
                }
            )
        status_css = {
            "Disponível": "status-pill status-active",
            "Manutenção": "status-pill status-maintenance",
            "Indisponível": "status-pill status-maintenance",
        }
        rows = [
            {
                "css": status_css.get(s["status"], "status-pill status-draft"),
                "latency": f"{s['latency_ms']} ms" if s.get("latency_ms") is not None else "—",
                "checked": relative(s["checked_at"], now) if s.get("checked_at") else "—",
                "item": dumps(
                    {"name": s["name"], "kind": s["kind"], "status": s["status"], "app": s.get("app")}
                ),
                "menu": f"health:{s['name']}",
                "detail": s.get("detail") or "",
                "name": s["name"],
                "kind": s["kind"],
                "status": s["status"],
            }
            for s in data.get("health", [])
        ]
        return render(
            request,
            "admin_health.html",
            "Operação",
            "Saúde da plataforma",
            "Serviços e integrações conectados.",
            rows=rows,
            slos=slos,
            has_latency=any(r["latency"] != "—" for r in rows),
            slo_meta=dumps(app_ids_meta()),
            meta=meta_json(),
            update=updates.notice(request.app.state),
            **await obs_ctx(request, app_q, env, win),
        )

    @api.get("/health")
    async def list_health() -> Any:
        return (await ops.read())["health"]

    @api.post("/health", status_code=201)
    async def create_service(request: Request) -> Any:
        return await ops.create_service(actor_of(request), await body(request))

    @api.post("/health/check")
    async def check_health(request: Request) -> Any:
        async def probe(app_id: str, upstream: str) -> tuple[str, bool, int]:
            t0 = time.monotonic()
            try:
                up = (await client.get(f"{upstream.rstrip('/')}/{app_id}", timeout=3)).status_code < 500
            except httpx.HTTPError:
                up = False
            return app_id, up, int((time.monotonic() - t0) * 1000)

        results = await asyncio.gather(*(probe(i, u) for i, u in insight.targets()))
        health = await ops.record_probes(actor_of(request), list(results))
        rows = [{"app": a, "available": up, "latency_ms": m} for a, up, m in results]
        return {"results": rows, "staging": [], "health": health}

    @api.put("/health/{name}")
    async def update_service(request: Request, name: str) -> Any:
        return await ops.update_service(actor_of(request), name, await body(request))

    @api.delete("/health/{name}", status_code=204)
    async def delete_service(request: Request, name: str) -> Response:
        await ops.delete_service(actor_of(request), name)
        return Response(status_code=204)

    @api.get("/slos")
    async def list_slos() -> Any:
        return (await store.cfg.read()).get("slos", [])

    @api.put("/slos")
    async def put_slo(request: Request) -> Any:
        return await ops.set_slo(actor_of(request), await body(request))

    @api.delete("/slos/{app_id}/{env}")
    async def delete_slo(request: Request, app_id: str, env: str) -> Any:
        await ops.delete_slo(actor_of(request), app_id, env)
        return {"deleted": True}

    @api.get("/incidents")
    async def list_incidents() -> Any:
        return await ops.incidents()

    @api.post("/incidents")
    async def create_incident(request: Request) -> Any:
        return await ops.create_incident(actor_of(request), await body(request))

    @api.post("/incidents/{iid}")
    async def update_incident(request: Request, iid: int) -> Any:
        b = await body(request)
        return await ops.update_incident(
            actor_of(request), iid, str(b.get("note", "")), bool(b.get("resolve"))
        )

    # ---- Feature flags ---------------------------------------------------------------------------------
    @app.get("/admin/flags")
    async def flags_page() -> Response:
        """A tela foi removida da interface (a API `/api/admin/flags` e a avaliação seguem)."""
        return RedirectResponse("/admin", status_code=308)

    @api.get("/flags")
    async def list_flags() -> Any:
        flags, version = await ops.flags()
        return {"flags": flags, "version": version}

    @api.post("/flags")
    async def create_flag(request: Request) -> Any:
        return await ops.upsert_flag(actor_of(request), await body(request), True)

    @api.put("/flags/{app_id}/{key}")
    async def update_flag(request: Request, app_id: str, key: str) -> Any:
        return await ops.upsert_flag(
            actor_of(request), (await body(request)) | {"app": app_id, "key": key}, False
        )

    @api.delete("/flags/{app_id}/{key}")
    async def delete_flag(request: Request, app_id: str, key: str) -> Any:
        await ops.delete_flag(actor_of(request), app_id, key)
        return {"deleted": key}

    @api.post("/flags/{app_id}/{key}/{env}/enabled")
    async def set_flag_enabled(request: Request, app_id: str, key: str, env: str) -> Any:
        b = await body(request)
        if not isinstance(b.get("enabled"), bool):
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        if env not in ("staging", "producao"):
            raise AdminError("Ambiente inválido.")
        return await ops.set_flag_enabled(actor_of(request), app_id, key, env, b["enabled"])

    # ---- Observabilidade -------------------------------------------------------------------------------
    @app.get("/admin/observability")
    async def old_observability(request: Request) -> Response:
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/admin/health{q}#obs", 308)

    async def obs_ctx(request: Request, app_q: str, env: str, min: str) -> dict[str, Any]:
        """Gráficos e requisições lentas da Saúde (filtros por app, ambiente e janela)."""
        allow = getattr(request.state, "apps", None)
        visible = lambda a: allow is None or a in allow  # noqa: E731
        apps_v = [a for a in refs() if visible(a["id"])]
        sel = app_q if any(a["id"] == app_q for a in apps_v) else (apps_v[0]["id"] if apps_v else "")
        env = "staging" if env == "staging" else "producao"
        minutes = int(min) if min in ("15", "30", "60") else 30
        chip = lambda on: "filter-chip active" if on else "filter-chip"  # noqa: E731
        url = lambda a, e, m: f"/admin/health?app={a}&env={e}&min={m}#obs"  # noqa: E731
        m = tel.metrics
        pts = m.points(sel, env, minutes)
        col = lambda k, f=lambda v: v: [None if p[k] is None else f(p[k]) for p in pts]  # noqa: E731
        charts = [
            chart("Requisições por segundo", "req/s", [("req/s", "var(--color-accent)", col("rps"))]),
            chart(
                "Taxa de erro (5xx)",
                "%",
                [("% erros", "var(--color-danger)", col("error_rate", lambda v: v * 100))],
            ),
            chart(
                "Latência (gateway → app)",
                "ms",
                [
                    ("p50", "var(--color-success)", col("p50")),
                    ("p95", "var(--color-warning)", col("p95")),
                    ("p99", "var(--color-danger)", col("p99")),
                ],
            ),
            chart("CPU do container", "vCPU", [("vCPU", "var(--color-violet)", col("cpu"))]),
            chart(
                "Memória do container",
                "MiB",
                [("MiB", "var(--color-info)", col("mem", lambda b: b / 1048576))],
            ),
        ]
        nowus = time.time_ns() // 1000
        traces = [
            {
                "name": x["name"],
                "app": f"{short(x['app'])} · {env_label(x['env'])}",
                "dur": ms_text(x["duration_us"]),
                "when": relative(x["start_us"] // 1000, nowus // 1000),
                "spans": x["spans"],
                "css": "status-pill status-maintenance" if x["error"] else "status-pill status-active",
                "status": x["status"],
            }
            for x in tel.traces.slow(sel, 15)
        ]
        otlp = (
            f"OTLP ligado: exportando traces e métricas para {tel.otlp}."
            if tel.otlp
            else "OTLP desligado (defina OTEL_EXPORTER_OTLP_ENDPOINT para enviar a um coletor)."
        )
        return {
            "app_chips": [(url(a["id"], env, minutes), a["short"], chip(a["id"] == sel)) for a in apps_v],
            "env_chips": [
                (url(sel, e, minutes), env_label(e), chip(e == env)) for e in ("producao", "staging")
            ],
            "win_chips": [(url(sel, env, mm), f"{mm} min", chip(mm == minutes)) for mm in (15, 30, 60)],
            "charts": charts,
            "traces": traces,
            "otlp": otlp,
        }

    # ---- Analytics -------------------------------------------------------------------------------------
    async def analytics_json() -> dict[str, Any]:
        s = an.summary(int(time.time() * 1000))
        prefs = app.state.ux.data.users
        return {
            "summary": s,
            "retention_days": an.retention,
            "raw_terms": an.raw_terms,
            "enabled": an.enabled,
            "opted_out": sum(1 for p in prefs.values() if p.analytics_opt_out),
            "storage_bytes": an.bytes_estimate(),
        }

    @app.get("/admin/analytics")
    async def old_analytics(request: Request) -> Response:
        # Quem só tem ver_auditoria (Product Owner) vê o cartão de uso, só leitura, na Auditoria.
        can_cfg = "editar_configuracoes" in getattr(request.state, "permissions", [])
        return RedirectResponse("/admin#uso" if can_cfg else "/admin/audit#uso", 308)

    @api.get("/analytics")
    async def analytics_get() -> Any:
        return await analytics_json()

    @app.get("/api/admin/analytics.csv")
    async def analytics_csv() -> Response:
        return Response(
            "﻿" + an.csv(int(time.time() * 1000)),
            media_type="text/csv; charset=utf-8",
            headers={"content-disposition": 'attachment; filename="analytics-de-uso.csv"'},
        )

    def need_settings(request: Request) -> None:
        if not can_edit(request):
            raise AdminError("Alterar o analytics exige “Editar configurações da plataforma”.", 403)

    @api.put("/analytics/retention")
    async def analytics_retention(request: Request) -> Any:
        need_settings(request)
        b = await body(request)
        if not isinstance(b.get("days"), int) or isinstance(b["days"], bool):
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        days = an.set_retention(b["days"])
        await store.audit(
            actor_of(request), "Retenção do analytics alterada", f"{days} dias", icon="settings"
        )
        return {"ok": True, "days": days}

    @api.put("/analytics/terms")
    async def analytics_terms(request: Request) -> Any:
        need_settings(request)
        b = await body(request)
        if not isinstance(b.get("raw"), bool):
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        an.set_raw_terms(b["raw"])
        detail = "texto guardado (opt-in)" if b["raw"] else "só contagem e hash"
        await store.audit(actor_of(request), "Termos de busca sem resultado", detail, icon="settings")
        return {"ok": True, "raw": b["raw"]}

    @api.post("/analytics/rotate-key")
    async def analytics_rotate(request: Request) -> Any:
        need_settings(request)
        an.rotate_key(int(time.time() * 1000))
        await store.audit(
            actor_of(request),
            "Chave dos ids do analytics trocada",
            "ids novos não se ligam aos anteriores",
            icon="settings",
        )
        return {"ok": True}

    # ---- Uso por usuário --------------------------------------------------------------------------------
    us = insight.usage

    async def usage_query(days: int, user: str, app_id: str) -> dict[str, Any]:
        q = us.query(int(time.time() * 1000), days, user.strip(), app_id.strip())
        async with app.state.auth.locked() as d:
            people = {u["id"]: (u.get("name") or "", u.get("email") or "") for u in d["users"]}
        names = {a["id"]: a["short"] for a in refs()} | {PLATFORM: "Hub"}
        for r in q["rows"] + q["by_user"] + q["by_app"]:
            if "user" in r:
                r["name"], r["email"] = people.get(r["user"], ("", ""))
            if "app" in r:
                r["app_name"] = names.get(r["app"], r["app"])
        return q | {"enabled": us.enabled}

    @api.get("/usage")
    async def usage_get(days: int = 30, user: str = "", app_id: str = Query("", alias="app")) -> Any:
        return await usage_query(days, user, app_id)

    @app.get("/api/admin/usage.csv")
    async def usage_csv(days: int = 30, user: str = "", app_id: str = Query("", alias="app")) -> Response:
        return Response(
            "﻿" + us.csv(int(time.time() * 1000), days, user.strip(), app_id.strip()),
            media_type="text/csv; charset=utf-8",
            headers={"content-disposition": 'attachment; filename="uso-por-usuario.csv"'},
        )

    @app.get("/admin/usage", response_class=HTMLResponse)
    async def usage_page(
        request: Request, days: int = 30, user: str = "", app_id: str = Query("", alias="app")
    ) -> Response:
        days = days if days in (7, 30, 90) else 30
        q = await usage_query(days, user, app_id)
        now = int(time.time() * 1000)
        for r in q["rows"] + q["by_user"] + q["by_app"]:
            r["when"] = relative(r["last"], now)
        rows, pager = paginate(request, q["rows"])
        async with app.state.auth.locked() as d:
            people = sorted((u["id"], u.get("name") or u.get("email") or u["id"]) for u in d["users"])
        apps = [(a["id"], a["short"]) for a in refs()] + [(PLATFORM, "Hub")]
        return render(
            request,
            "admin_usage.html",
            "Governança",
            "Uso por usuário",
            "Quem usa cada aplicação: aberturas, navegações, ações e último acesso, coletados pelo gateway.",
            q=q,
            rows=rows,
            pager=pager,
            days=days,
            user=user,
            app_id=app_id,
            people=sorted(people, key=lambda p: p[1].lower()),
            apps=apps,
        )

    @app.middleware("http")
    async def api_spans(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if (
            not enabled()
            or request.method in ("GET", "HEAD", "OPTIONS")
            or not request.url.path.startswith("/api/")
        ):
            return await call_next(request)
        t0, us = time.monotonic(), time.time_ns() // 1000
        resp = await call_next(request)
        insight.span_for_api(request, resp.status_code, t0, us)
        return resp

    app.include_router(api)
