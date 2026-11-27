"""Operations API: remove an application (`POST /api/ops/apps/{id}/remove`) or every sample app at once.

The body is the confirmation typed in the modal; a browser session also confirms with the user's password (step-up).
A token (API/MCP) has no password to give: the MCP confirmation (`confirm_token`) stands in for it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hub.auth.stepup import check_password
from hub.ops import remove_app as ra
from hub.ops.model import OpsError
from hub.ops.opsctx import OpsCtx, ctx
from hub.ops.remove_state import confirms

router = APIRouter()
SAMPLES_PHRASES = ("apps de exemplo", "sample apps")  # what the samples modal asks to type (pt-BR, en)


class RemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str = Field(min_length=1, max_length=200, description="Nome ou id da aplicação (confirmação).")
    backup: bool = True
    keep_data: bool = False
    password: str = Field(default="", max_length=256)


async def _checked(request: Request, ops: OpsCtx) -> RemoveIn:
    """The closed body, and the password when the request comes from a browser session."""
    try:
        body = RemoveIn.model_validate(await ops.body(request))
    except ValidationError as e:
        raise OpsError("Dados inválidos: verifique os campos enviados.") from e
    if request.state.token is None:
        wrong = "Senha incorreta: a remoção não foi confirmada."
        if err := await check_password(request, body.password, wrong):
            raise OpsError(err, 403)
    return body


async def _accepted(ops: OpsCtx, apps: list[str], actor: str, body: RemoveIn) -> JSONResponse:
    job = await ra.start(ops, apps, actor, body.backup, body.keep_data)
    return JSONResponse(job, status_code=202)


@router.post("/apps/remove-samples")
async def remove_samples(request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    apps = ra.samples(ops)
    if not apps:
        raise OpsError("Nenhum app de exemplo neste hub.", 404)
    body = await _checked(request, ops)
    if " ".join(body.confirm.split()).casefold() not in SAMPLES_PHRASES:
        raise OpsError("Confirme digitando apps de exemplo.")
    return await _accepted(ops, apps, ops.actor_of(request), body)


@router.post("/apps/{app_id}/remove")
async def remove_app(app_id: str, request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    e = ops.need_app(app_id)
    body = await _checked(request, ops)
    if not confirms(body.confirm, app_id, e.name):
        raise OpsError(f"Confirme digitando o nome da aplicação ({e.name}).")
    return await _accepted(ops, [app_id], ops.actor_of(request), body)


def mount(ops: OpsCtx) -> None:
    """Nothing to wire at startup (same contract as the other sections)."""
