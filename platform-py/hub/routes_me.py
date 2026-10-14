"""`/api/me/tokens`: personal API tokens, managed only from a browser session."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from hub.auth import now_ms
from hub.models import TokenCreateInput
from hub.webctx import WebCtx, web

router = APIRouter()
_SESSION_ONLY = "Tokens pessoais só podem ser geridos com a sessão."


@router.get("/api/me/tokens")
async def my_tokens(request: Request, w: WebCtx = Depends(web)) -> dict[str, Any]:
    if request.state.token:
        raise HTTPException(403, _SESSION_ONLY)
    async with w.auth_store.locked() as d:
        return {
            "items": [
                {k: v for k, v in t.items() if k != "hash"}
                | {
                    "active": t.get("revoked_at") is None
                    and t.get("expires_at", 0) > time.time_ns() // 1_000_000
                }
                for t in d["tokens"]
                if t.get("owner") == request.state.user["id"]
            ]
        }


@router.post("/api/me/tokens")
async def new_token(request: Request, w: WebCtx = Depends(web)) -> JSONResponse:
    if request.state.token:
        return JSONResponse({"error": _SESSION_ONLY}, status_code=403)
    try:
        data = TokenCreateInput.model_validate(await request.json())
    except (ValidationError, ValueError):
        return JSONResponse({"error": "Dados de token inválidos."}, status_code=400)
    actions, apps = data.actions, data.apps
    if request.state.apps is not None and any(app_id not in request.state.apps for app_id in apps):
        return JSONResponse({"error": "Você não tem acesso a um dos apps informados."}, status_code=403)
    secret, item = await w.auth_store.create_token(
        request.state.user, data.name, apps, actions, data.expires_days
    )
    if admin_store := getattr(request.app.state, "admin", None):
        await admin_store.audit(
            request.state.user["name"], "Token pessoal criado", item["name"], icon="users"
        )
    return JSONResponse(
        {"token": secret, "item": {k: v for k, v in item.items() if k != "hash"}}, status_code=201
    )


@router.post("/api/me/tokens/{token_id}/revoke")
async def revoke_token(request: Request, token_id: str, w: WebCtx = Depends(web)) -> JSONResponse:
    if request.state.token:
        return JSONResponse({"error": _SESSION_ONLY}, status_code=403)
    async with w.auth_store.locked(True) as d:
        item = next(
            (t for t in d["tokens"] if t["id"] == token_id and t.get("owner") == request.state.user["id"]),
            None,
        )
        if not item:
            return JSONResponse({"error": "Token não encontrado."}, status_code=404)
        item["revoked_at"] = now_ms()
        if admin_store := getattr(request.app.state, "admin", None):
            await admin_store.audit(
                request.state.user["name"], "Token pessoal revogado", token_id, icon="users"
            )
        return JSONResponse({k: v for k, v in item.items() if k != "hash"})
