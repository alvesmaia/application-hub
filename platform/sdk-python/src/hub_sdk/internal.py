"""Endpoints internos que **só o hub** usa, pela rede interna do app:

====== ========================================= =====================================
Método Rota
====== ========================================= =====================================
GET    ``/{app}/_hub/outbox?after=&limit=``      eventos pendentes (cursor = ``id``)
POST   ``/{app}/_hub/outbox/ack``                ``{"ids": ["evt-…"]}`` (≤ 1000)
GET    ``/{app}/_hub/jobs?limit=``               tarefas (mais recentes primeiro)
POST   ``/{app}/_hub/jobs/{id}/retry|cancel``    reprocessar / cancelar
GET    ``/{app}/_hub/telemetry?after=``         spans novos + medidores (onda 8)
POST   ``/{app}/_hub/flags``                    definições das feature flags (onda 8)
GET    ``/{app}/_hub/search?q=&limit=``          busca federada com a identidade (onda 9)
POST   ``/{app}/_hub/approvals/{id}``            decisão de um pedido de aprovação (onda 9)
====== ========================================= =====================================

Toda chamada exige ``x-hub-internal-token`` igual a ``HUB_INTERNAL_TOKEN`` (comparação em
tempo constante); sem token configurado (ou < 16 caracteres) tudo responde **403**, token
errado **401**. ``limit`` é limitado a 500. O gateway do hub recusa ``/_frag/{app}/_hub/*``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hub_sdk import approvals as _ap
from hub_sdk import locale as _locale
from hub_sdk import search as _search
from hub_sdk.flags import FlagStore, validate_push
from hub_sdk.identity import USER_HEADER, parse_identity
from hub_sdk.jobs import Jobs
from hub_sdk.outbox import Outbox
from hub_sdk.schema import ct_eq
from hub_sdk.telemetry import Telemetry

TOKEN_HEADER = "x-hub-internal-token"  # noqa: S105 — nome do cabeçalho, não um segredo
SEGMENT = "_hub"
MAX_PAGE = 500
MAX_ACK = 1000


def token_from_env() -> str | None:
    t = os.environ.get("HUB_INTERNAL_TOKEN", "").strip()
    return t if len(t) >= 16 else None


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def router(
    app_id: str,
    token: str | None,
    outbox: Outbox | Callable[[], Outbox],
    jobs: Jobs | Callable[[], Jobs | None] | None,
    wake: Callable[[], None] | None = None,
    telemetry: Telemetry | None = None,
    flags: FlagStore | None = None,
    search: _search.SearchFn | None = None,
    approvals: _ap.ApprovalFn | None = None,
) -> APIRouter:
    """``outbox``/``jobs`` podem ser funções sem argumentos (resolvidas a cada chamada:
    o lifespan troca os backends em memória pelos do Postgres)."""
    r = APIRouter(include_in_schema=False)

    def ob() -> Outbox:
        return outbox if isinstance(outbox, Outbox) else outbox()

    def jb() -> Jobs | None:
        return jobs if jobs is None or isinstance(jobs, Jobs) else jobs()

    base = f"/{app_id}/{SEGMENT}"

    def auth(req: Request) -> JSONResponse | None:
        if token is None:
            return _err(403, "endpoints internos desabilitados: HUB_INTERNAL_TOKEN não configurado")
        sent = req.headers.get(TOKEN_HEADER, "")
        if not ct_eq(sent.encode("latin-1", "replace"), token.encode()):
            return _err(401, "token interno inválido")
        return None

    @r.get(f"{base}/outbox")
    async def outbox_list(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        after = _int(req.query_params.get("after"), 0)
        limit = max(1, min(_int(req.query_params.get("limit"), 200), MAX_PAGE))
        try:
            ev = await ob().pending(after, limit)
        except Exception as ex:
            return _err(503, str(ex))
        return {"events": [e.to_json() for e in ev], "durable": ob().durable}

    @r.post(f"{base}/outbox/ack")
    async def outbox_ack(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        try:
            body = await req.json()
            ids = body["ids"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return _err(422, 'corpo inválido: use {"ids": ["evt-…"]}')
        if len(ids) > MAX_ACK:
            return _err(400, "no máximo 1000 ids por confirmação")
        try:
            n = await ob().ack(ids)
        except Exception as ex:
            return _err(503, str(ex))
        return {"acked": n}

    @r.get(f"{base}/jobs")
    async def jobs_list(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        jobs_ = jb()
        if jobs_ is None:
            return {"jobs": [], "enabled": False}
        limit = max(1, min(_int(req.query_params.get("limit"), 100), MAX_PAGE))
        try:
            lst = await jobs_.list(limit)
        except Exception as ex:
            return _err(503, str(ex))
        return {"jobs": [j.to_json() for j in lst], "enabled": True}

    @r.post(base + "/jobs/{job_id}/{action}")
    async def job_action(req: Request, job_id: str, action: str) -> Any:
        if (e := auth(req)) is not None:
            return e
        jobs_ = jb()
        if jobs_ is None:
            return _err(404, "este app não tem tarefas duráveis")
        try:
            if action == "retry":
                j = await jobs_.retry(job_id)
            elif action == "cancel":
                j = await jobs_.cancel(job_id)
            else:
                return _err(404, "ação desconhecida")
        except LookupError as ex:
            return _err(404, str(ex))
        except ValueError as ex:
            return _err(409, str(ex))
        except Exception as ex:
            return _err(503, str(ex))
        if wake is not None:
            wake()
        return {"job": j.to_json()}

    @r.get(f"{base}/telemetry")
    async def telemetry_pull(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        after = max(0, _int(req.query_params.get("after"), 0))
        if telemetry is None:
            return {"spans": [], "next": after, "gauges": {}}
        spans, nxt = telemetry.pending(after, 1000)
        return {"spans": spans, "next": nxt, "gauges": telemetry.gauges()}

    @r.post(f"{base}/flags")
    async def flags_push(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        if flags is None:
            return _err(404, "este app não usa feature flags")
        try:
            body = await req.json()
        except ValueError:
            return _err(400, "definições de flags inválidas")
        parsed = validate_push(body)
        if parsed is None:
            return _err(400, "definições de flags inválidas")
        v, env, fl = parsed
        applied = flags.replace(v, env, fl)
        return {"applied": applied, "flags": len(fl), "version": flags.version}

    @r.get(f"{base}/search")
    async def search_ep(req: Request) -> Any:
        if (e := auth(req)) is not None:
            return e
        if search is None:
            return _err(404, "este app não oferece busca")
        q = (req.query_params.get("q") or "").strip()
        if not q or len(q) > _search.MAX_QUERY:
            return _err(400, "consulta vazia ou longa demais")
        limit = max(1, min(_int(req.query_params.get("limit"), 5), _search.MAX_LIMIT))
        query = _search.SearchQuery(
            q=q,
            limit=limit,
            identity=parse_identity(req.headers.get(USER_HEADER)),
            locale=_locale.from_headers(req.headers),
        )
        token_ = _locale.use(query.locale)
        try:
            hits = await search(query)
        except Exception as ex:
            return _err(503, str(ex))
        finally:
            _locale._current.reset(token_)
        return {"results": [h.to_json() for h in _search.sanitize(app_id, hits, limit)]}

    @r.post(base + "/approvals/{approval_id}")
    async def approval_ep(req: Request, approval_id: str) -> Any:
        if (e := auth(req)) is not None:
            return e
        if approvals is None:
            return _err(404, "este app não recebe aprovações")
        if not _ap.valid_id(approval_id):
            return _err(400, "id de aprovação inválido")
        try:
            body = await req.json()
            decision = body["decision"]
            if decision not in ("approve", "reject"):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return _err(422, 'corpo inválido: use {"decision": "approve"|"reject"}')
        approver = parse_identity(req.headers.get(USER_HEADER))
        if approver is None:
            return _err(403, "aprovador ausente (x-hub-user)")
        if not approver.has_permission(_ap.APPROVE_PERMISSION):
            return _err(403, "o aprovador não tem a permissão aprovar_solicitacoes")
        d = _ap.ApprovalDecision(
            approval_id=approval_id,
            decision=decision,
            comment="".join(c for c in str(body.get("comment", "")) if c.isprintable())[:500],
            event_id=str(body.get("event_id", ""))[:80],
            decided_at=_int(str(body.get("decided_at", "0")), 0),
            approver=approver,
        )
        try:
            status = await approvals(d)
        except _ap.NotFound:
            return _err(404, "pedido desconhecido")
        except _ap.AlreadyDecided as ex:
            return JSONResponse({"status": ex.status, "error": "pedido já decidido"}, status_code=409)
        except _ap.Forbidden as ex:
            return _err(403, str(ex))
        except Exception as ex:
            return _err(503, str(ex))
        return {"status": status}

    return r
