"""`[api]`, `[mcp]` and `[mcp.oauth]` of hub.toml (defaults = config/hub.example.toml) + HUB_* variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_TRUE, _FALSE = {"1", "true", "yes", "on", "sim"}, {"0", "false", "no", "off", "nao", "não"}


@dataclass
class OAuthCfg:
    enabled: bool = True
    dynamic_registration: bool = True
    client_metadata_documents: bool = True
    allowed_redirect_hosts: list[str] = field(default_factory=list)
    allowed_scopes: list[str] = field(default_factory=lambda: ["mcp", "api:write"])
    access_token_ttl_s: int = 3600
    refresh_token_ttl_days: int = 30
    code_ttl_s: int = 120


@dataclass
class McpCfg:
    enabled: bool = True
    transports: list[str] = field(default_factory=lambda: ["http", "stdio"])
    path: str = "/mcp"
    destructive_actions: str = "confirm"
    expose_app_tools: bool = True
    public_network: bool = False
    calls_per_min: int = 120
    tool_timeout_s: int = 30
    max_result_kb: int = 256
    logs_tail_max: int = 200
    confirm_ttl_s: int = 300
    max_request_kb: int = 256
    oauth: OAuthCfg = field(default_factory=OAuthCfg)


@dataclass
class ApiCfg:
    enabled: bool = True
    docs: bool = True
    rate_per_min: int = 600
    page_size: int = 50
    max_page_size: int = 500


@dataclass
class HubCfg:
    api: ApiCfg = field(default_factory=ApiCfg)
    mcp: McpCfg = field(default_factory=McpCfg)

    @property
    def mcp_http(self) -> bool:
        return self.mcp.enabled and "http" in self.mcp.transports

    @property
    def oauth_on(self) -> bool:
        return self.mcp_http and self.mcp.oauth.enabled


def _flag(env: Mapping[str, str], name: str, cur: bool) -> bool:
    v = env.get(name, "").strip().lower()
    if not v:
        return cur
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"{name}={v!r}: use true/false")


def _num(env: Mapping[str, str], name: str, cur: int) -> int:
    v = env.get(name, "").strip()
    try:
        return int(v) if v else cur
    except ValueError:
        raise ValueError(f"{name}={v!r}: número inválido") from None


def _fill(obj: Any, data: Mapping[str, Any]) -> None:
    for k, v in data.items():
        if hasattr(obj, k) and not isinstance(v, dict):
            setattr(obj, k, v)


def _from_toml(c: HubCfg, toml: Mapping[str, Any]) -> None:
    api, mcp = dict(toml.get("api", {})), dict(toml.get("mcp", {}))
    _fill(c.api, api)
    c.api.docs = bool(api.get("docs", {}).get("enabled", True))
    _fill(c.mcp, mcp)
    _fill(c.mcp, mcp.get("limits", {}))
    _fill(c.mcp.oauth, mcp.get("oauth", {}))


def _api_env(a: ApiCfg, env: Mapping[str, str]) -> None:
    a.enabled = _flag(env, "HUB_API_ENABLED", a.enabled)
    a.docs = _flag(env, "HUB_API_DOCS_ENABLED", a.docs)
    a.rate_per_min = _num(env, "HUB_API_RATE_PER_MIN", a.rate_per_min)


def _mcp_env(m: McpCfg, env: Mapping[str, str]) -> None:
    m.enabled = _flag(env, "HUB_MCP_ENABLED", m.enabled)
    m.expose_app_tools = _flag(env, "HUB_MCP_EXPOSE_APP_TOOLS", m.expose_app_tools)
    m.public_network = _flag(env, "HUB_MCP_PUBLIC_NETWORK", m.public_network)
    m.path = env.get("HUB_MCP_PATH", "").strip() or m.path
    if v := env.get("HUB_MCP_TRANSPORTS"):
        m.transports = [s.strip() for s in v.split(",") if s.strip()]
    m.destructive_actions = env.get("HUB_MCP_DESTRUCTIVE_ACTIONS", "").strip() or m.destructive_actions
    m.calls_per_min = _num(env, "HUB_MCP_CALLS_PER_MIN", m.calls_per_min)
    m.tool_timeout_s = _num(env, "HUB_MCP_TOOL_TIMEOUT_S", m.tool_timeout_s)
    m.max_result_kb = _num(env, "HUB_MCP_MAX_RESULT_KB", m.max_result_kb)
    m.confirm_ttl_s = _num(env, "HUB_MCP_CONFIRM_TTL_S", m.confirm_ttl_s)


def _oauth_env(o: OAuthCfg, env: Mapping[str, str]) -> None:
    o.enabled = _flag(env, "HUB_MCP_OAUTH_ENABLED", o.enabled)
    o.dynamic_registration = _flag(env, "HUB_MCP_OAUTH_DYNAMIC_REGISTRATION", o.dynamic_registration)
    o.client_metadata_documents = _flag(
        env, "HUB_MCP_OAUTH_CLIENT_METADATA_DOCUMENTS", o.client_metadata_documents
    )
    if v := env.get("HUB_MCP_OAUTH_ALLOWED_REDIRECT_HOSTS"):
        o.allowed_redirect_hosts = [s.strip().lower() for s in v.split(",") if s.strip()]
    o.access_token_ttl_s = _num(env, "HUB_MCP_OAUTH_ACCESS_TOKEN_TTL_S", o.access_token_ttl_s)


def _validate(m: McpCfg) -> None:
    p = m.path
    rest = p.removeprefix("/mcp/")
    if p != "/mcp" and not (
        p.startswith("/mcp/")
        and rest
        and "/" not in rest
        and len(rest) <= 32
        and all(ch.islower() or ch.isdigit() or ch == "-" for ch in rest)
    ):
        raise ValueError(f'[mcp].path = {p!r}: use "/mcp" ou "/mcp/<nome>"')
    if m.destructive_actions not in ("confirm", "deny"):
        raise ValueError("destructive_actions: confirm ou deny")
    if any(t not in ("http", "stdio") for t in m.transports) or (
        "stdio" in m.transports and "http" not in m.transports
    ):
        raise ValueError("[mcp].transports: http e/ou stdio (stdio exige http)")


def load_cfg(toml: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None) -> HubCfg:
    toml, env = toml or {}, os.environ if env is None else env
    c = HubCfg()
    _from_toml(c, toml)
    _api_env(c.api, env)
    _mcp_env(c.mcp, env)
    _oauth_env(c.mcp.oauth, env)
    _validate(c.mcp)
    return c
