"""Public API v1 components of the operations and Administration writes (registered by `schemas.schemas()`)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from hub.api.schemas import Out, U


class UploadLink(Out):
    """One-time upload link for a `.hubapp` package (valid for 10 minutes, bound to who asked for it)."""

    upload_url: str
    expires_at: U
    method: str
    field: str
    max_mb: U
    curl: str
    next: str
    env: str | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,40}$")


MODELS: tuple[type, ...] = (UploadLink,)

EXAMPLES: dict[str, Any] = {
    "UploadLink": {
        "upload_url": "https://hub.exemplo.com/mcp/uploads/3q2-exemplo",
        "expires_at": 1760000600000,
        "method": "POST",
        "field": "package",
        "max_mb": 20,
        "curl": "curl -fsS -F package=@app.hubapp 'https://hub.exemplo.com/mcp/uploads/3q2-exemplo'",
        "next": "Acompanhe com jobs_get (id do job na resposta) ou deployments_list.",
        "env": "staging",
    },
}
