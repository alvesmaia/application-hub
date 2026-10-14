"""Logs (page, query, live stream) and Audit (page, query, verification, export)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from hub.admin.routes import relative
from hub.admin.store import AdminStore
from hub.insight.insightctx import InsightCtx, ctx
from hub.insight.view import split_ids
from hub.ux.pager import paginate

pages = APIRouter()
router = APIRouter()  # under /api/admin (JSON only)

_LEVELS = [("", "Todos"), ("ERROR", "Erro"), ("WARN", "Aviso"), ("WARN,ERROR", "Aviso ou erro"),
           ("INFO", "Informação")]  # fmt: skip
_SOURCES = [("", "Todas"), ("gateway", "Requisições"), ("container", "Saída dos contêineres"),
            ("app", "Eventos dos apps"), ("auth", "Acesso"), ("admin", "Administração"), ("ops", "Operações")]  # fmt: skip
_RANGES = [("60", "Última hora"), ("1440", "Últimas 24 horas"), ("10080", "Últimos 7 dias"),
           ("43200", "Últimos 30 dias")]  # fmt: skip
_LEVEL_CSS = {"ERROR": "log-level log-level-error", "WARN": "log-level log-level-warn"}


# ---- Logs ------------------------------------------------------------------------------------------
@pages.get("/admin/logs", response_class=HTMLResponse)
async def logs_page(request: Request, c: InsightCtx = Depends(ctx)) -> Response:
    lines = [
        {
            "time": x["time"],
            "level": x["level"],
            "css": _LEVEL_CSS.get(x["level"], "log-level log-level-info"),
            "app": c.short(x["app"]),
            "user": f"@{x['user']}" if x["user"] else "",
            "message": x["message"],
        }
        for x in c.store.logs.query({"minutes": "60"})
    ]
    apps = [("all", "Todas as aplicações"), ("platform", "Plataforma")] + [
        (a["id"], a["name"]) for a in c.refs()
    ]
    return c.render(
        request,
        "admin_logs.html",
        "Operação",
        "Logs da plataforma",
        "Registros reais: requisições, acessos, operações e a saída dos contêineres dos apps. Filtre ou acompanhe em tempo real.",
        tab="Logs",
        lines=lines,
        apps=apps,
        levels=_LEVELS,
        sources=_SOURCES,
        ranges=_RANGES,
        meta=c.meta_json(),
    )


@router.get("/logs")
async def list_logs(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    return c.store.logs.query(dict(request.query_params))


@router.get("/logs/stats")
async def log_stats(c: InsightCtx = Depends(ctx)) -> Any:
    return {"subscribers": len(c.store.logs.subscribers)}


@router.get("/logs/stream")
async def stream_logs(request: Request, c: InsightCtx = Depends(ctx)) -> Response:
    f = {k: v for k, v in request.query_params.items() if k != "minutes"}
    return StreamingResponse(
        _log_events(c.store, f),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


async def _log_events(store: AdminStore, f: dict[str, str]) -> AsyncIterator[str]:
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


# ---- Auditoria -------------------------------------------------------------------------------------
@pages.get("/admin/audit", response_class=HTMLResponse)
async def audit_page(request: Request, scope: str = "", c: InsightCtx = Depends(ctx)) -> Response:
    perms = getattr(request.state, "permissions", [])
    ids = {a["id"] for a in c.refs()}
    flt = scope if scope == "platform" or scope in ids else ""
    chips = [("", "Todas", not flt), ("platform", "Plataforma", flt == "platform")]
    chips += [(a["id"], a["short"], flt == a["id"]) for a in c.refs()]
    now = int(time.time() * 1000)
    rows = [
        {
            "icon": e["icon"],
            "title": e["title"],
            "detail": split_ids(f"{e['actor']} · {e['detail']} · {c.short(e['scope'])}"),
            "time": relative(e["at"], now),
        }
        for e in [e for e in await c.trail() if not flt or e["scope"] == flt][:100]
    ]
    rows, pager = paginate(request, rows)
    return c.render(
        request,
        "admin_audit.html",
        "Governança",
        "Auditoria e atividade",
        "Eventos administrativos recentes, filtrados por aplicação. A trilha é encadeada por hashes e assinada: "
        "qualquer edição, remoção ou reordenação é detectada.",
        tab="Auditoria",
        chips=chips,
        rows=rows,
        blocked=c.store.chain.blocked,
        published=_published(request),
        meta=c.meta_json(),
        pager=pager,
        usage=None if "editar_configuracoes" in perms else c.usage_of(request),
    )


def _published(request: Request) -> tuple[str, Any, bool]:
    """Rollback protection card: where the signed head of the audit chain is published (if anywhere)."""
    chain = request.app.state.admin.chain
    target = chain.head_target()
    return (
        ("Proteção contra rollback: âncora externa", f"Cabeça assinada publicada em {target}.", False)
        if target and not chain.head_error
        else ("Proteção contra rollback: falha na âncora externa", chain.head_error, True)
        if target
        else None
    ) or (
        "Proteção contra rollback: só local",
        "Sem publicação externa do head da auditoria, um rollback do diretório de dados inteiro não é detectado. "
        "Para ativar, defina HUB_AUDIT_HEAD_URL ou HUB_AUDIT_HEAD_FILE (FAQ 16).",
        False,
    )


@router.get("/audit")
async def list_audit(scope: str = "", limit: int = 100, c: InsightCtx = Depends(ctx)) -> Any:
    limit = min(max(limit, 1), 5000)
    return [e for e in await c.trail() if scope in ("", "all") or e["scope"] == scope][:limit]


@router.post("/audit/verify")
async def verify_audit(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    report = await asyncio.to_thread(c.store.chain.report)
    detail = (
        f"Íntegra: {report['entries']} eventos, {report['checkpoints']} checkpoints"
        if report["ok"]
        else f"{len(report['problems'])} problema(s): {report['problems'][0]}"
    )
    with contextlib.suppress(RuntimeError):  # chain blocked: cannot record (the report already says why)
        await c.store.audit(c.actor_of(request), "Integridade da auditoria verificada", detail, icon="check")
    return report


@router.get("/audit/export")
async def export_audit(request: Request, c: InsightCtx = Depends(ctx)) -> Response:
    text = await asyncio.to_thread(c.store.chain.export)
    await c.store.audit(c.actor_of(request), "Auditoria exportada (JSONL com provas)", "", icon="file")
    return Response(
        text,
        media_type="application/x-ndjson",
        headers={"content-disposition": "attachment; filename=auditoria.jsonl"},
    )
