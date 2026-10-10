"""Hub de teste da API pública, do MCP e do OAuth: apps com manifesto, sessões por função e tokens por dono."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from admin_helper import HDR, add_user
from auth_helper import client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.config import Settings
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import AppEntry, Registry
from hub.models import AppManifest

__all__ = ["HDR", "add_user", "api_hub", "bearer", "make_token", "rpc"]

APPS = ("ativos", "servicos")
_ENV = (
    "HUB_PUBLIC_URL", "HUB_ADMIN_ALLOWED_CIDRS", "HUB_API_ENABLED", "HUB_API_DOCS_ENABLED", "HUB_API_RATE_PER_MIN",
    "HUB_MCP_ENABLED", "HUB_MCP_PUBLIC_NETWORK", "HUB_MCP_DESTRUCTIVE_ACTIONS", "HUB_MCP_ALLOWED_ORIGINS",
    "HUB_MCP_CALLS_PER_MIN", "HUB_MCP_MAX_RESULT_KB", "HUB_MCP_OAUTH_ENABLED", "HUB_MCP_OAUTH_DYNAMIC_REGISTRATION",
    "HUB_MCP_OAUTH_ALLOWED_REDIRECT_HOSTS", "HUB_LOG_MCP_REQUESTS", "HUB_MCP_EXPOSE_APP_TOOLS",
)  # fmt: skip


def registry() -> Registry:
    return Registry(
        [
            AppEntry(
                id=a,
                name=f"App {a.title()}",
                upstream="http://upstream",
                version="1.0.0",
                display={"short": a.title(), "color": "#e0f2f1", "ink": "#0f766e", "icon": "box"},
                manifest=AppManifest.model_validate(
                    {"id": a, "name": f"App {a.title()}", "version": "1.0.0", "runtime": {"stack": "python"}}
                ),
            )
            for a in APPS
        ]
    )


def api_hub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    handler: Any = None,
    upstream: Any = None,
) -> TestClient:
    """Cliente com a sessão do Administrador (cabeçalhos de mesma origem)."""
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    transport = (
        httpx.ASGITransport(app=upstream)
        if upstream
        else httpx.MockTransport(handler or (lambda r: httpx.Response(404)))
    )
    gw = Gateway(registry(), GwOptions(user_sig_key=b"k" * 32), transport=transport)
    app = create_app(Settings("Application Hub", toml or {}, ()), gw)
    c = client_for(app)
    c.headers.update(HDR)
    return c


def make_token(c: TestClient, actions: list[str], apps: list[str] | None = None, name: str = "t") -> str:
    r = c.post("/api/me/tokens", json={"name": name, "apps": apps or [], "actions": actions})
    assert r.status_code == 201, r.text
    return str(r.json()["token"])


def bearer(c: TestClient, token: str, **headers: str) -> TestClient:
    """Cliente só com `Authorization: Bearer` (sem cookie)."""
    b = TestClient(c.app, follow_redirects=False)
    b.headers["authorization"] = f"Bearer {token}"
    b.headers.update(headers)
    return b


def rpc(
    c: TestClient, method: str, params: dict[str, Any] | None = None, /, **headers: str
) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return cast(httpx.Response, c.post("/mcp", json=body, headers={"accept": "application/json", **headers}))
