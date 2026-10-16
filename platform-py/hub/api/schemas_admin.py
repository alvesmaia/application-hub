"""Public API v1 components of the operations and Administration writes (registered by `schemas.schemas()`).

Bodies (`In`) are closed and checked at the edge (`v1.call`); responses (`Out`) are open. Secrets are write-only:
no response here carries one (the internal routes mask them)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue

from hub.api.schemas import Env, In, Out, U

EXAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).parent / "examples_admin.json").read_text(encoding="utf-8")
)
_SECRET: dict[str, JsonValue] = {"format": "password", "writeOnly": True}


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


class Done(Out):
    """Result of an action (empty when the internal route answers 204)."""


# ---- users and roles ------------------------------------------------------------------------------------
class RoleCreate(In):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    level: Literal["Total", "Alto", "Padrão"]
    members: U = 0


class RoleUpdate(In):
    description: str = Field(default="", max_length=200)
    level: Literal["Total", "Alto", "Padrão"]
    members: U = 0


class PermissionGrant(In):
    granted: bool


class RoleApps(In):
    apps: list[str] | None = Field(description="Apps da função; null = todos")


class GrantRevoke(In):
    item: int | str = Field(description="Id do pedido na Inbox (16 ou i:16)")


# ---- variables, settings, tokens ------------------------------------------------------------------------
class BindingIn(In):
    app: str = Field(min_length=1, max_length=40)
    env: Env


class VariableCreate(In):
    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    value: str = Field(min_length=1, max_length=4096, json_schema_extra=_SECRET)
    kind: Literal["publica", "secreta", "connection_string"] = "publica"
    bindings: list[BindingIn] = Field(default_factory=list)
    inject_as: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")


class VariableUpdate(In):
    value: str = Field(default="", max_length=4096, json_schema_extra=_SECRET)
    kind: Literal["publica", "secreta", "connection_string"]
    bindings: list[BindingIn]
    inject_as: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")


class SecretRotated(Out):
    variable: str
    restart_pending: bool


class Settings(Out):
    platform_name: str
    brand_color: str
    status_public: bool
    status_public_apps: list[str]
    language: str
    timezone: str


class SettingsUpdate(In):
    platform_name: str = Field(default="", max_length=60)
    brand_color: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    status_public: bool = False
    status_public_apps: list[str] = Field(default_factory=list)
    language: Literal["pt-BR", "en"]
    timezone: Literal["America/Sao_Paulo"]


class ApiToken(Out):
    id: str
    name: str
    active: bool
    prefix: str | None = None
    apps: list[str] | None = None
    envs: list[str] | None = None
    actions: list[str] | None = None
    created_by: str | None = None
    created_at: U | None = None
    expires_at: U | None = None
    revoked_at: U | None = None
    last_used: U | None = None
    owner: str | None = None


# ---- health, SLOs, incidents ----------------------------------------------------------------------------
_KIND = Literal["Aplicação", "Serviço", "Identity"]
_STATUS = Literal["Disponível", "Manutenção", "Indisponível"]


class HealthService(Out):
    name: str
    kind: str
    status: str
    app: str | None = None
    checked_at: U | None = None
    latency_ms: U | None = None


class HealthServiceUpdate(In):
    kind: _KIND
    status: _STATUS
    app: str | None = None


class HealthServiceCreate(HealthServiceUpdate):
    name: str = Field(min_length=1, max_length=60)


class HealthCheck(Out):
    results: list[dict[str, Any]]
    health: list[dict[str, Any]] = Field(default_factory=list)


class Slo(Out):
    app: str
    env: str
    availability: float
    latency_ms: U
    window_days: U
    latency_target: float | None = None


class SloSet(In):
    app: str = Field(min_length=1, max_length=40)
    env: Env
    availability: float = Field(ge=50, le=99.999)
    latency_ms: int = Field(ge=1, le=60_000)
    latency_target: float = Field(default=95.0, ge=50, le=99.999)
    window_days: Literal[7, 30]


class Incident(Out):
    id: U
    app: str
    env: str
    title: str
    severity: str
    started: U
    resolved: U | None = None
    kind: str | None = None
    notes: list[dict[str, Any]] = Field(default_factory=list)


class IncidentCreate(In):
    app: str = Field(min_length=1, max_length=40, description="Id do app ou platform")
    env: Env = Env.producao
    title: str = Field(min_length=1, max_length=120)
    severity: Literal["major", "minor", "info"]
    note: str = Field(default="", max_length=2000)


class IncidentUpdate(In):
    note: str = Field(default="", max_length=2000)
    resolve: bool = False


# ---- feature flags --------------------------------------------------------------------------------------
class FlagRuleIn(In):
    roles: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    serve: str


class FlagRolloutIn(In):
    percent: int = Field(ge=0, le=100)
    serve: str


class FlagEnvIn(In):
    enabled: bool = False
    default: str
    rules: list[FlagRuleIn] = Field(default_factory=list)
    rollout: FlagRolloutIn | None = None


class FlagUpdate(In):
    description: str = Field(default="", max_length=200)
    kind: Literal["boolean", "variant"] = "boolean"
    variants: list[str] = Field(default_factory=list)
    envs: dict[str, FlagEnvIn] = Field(default_factory=dict)


class FlagCreate(FlagUpdate):
    key: str = Field(pattern=r"^[a-z0-9._-]{1,64}$")
    app: str = Field(min_length=1, max_length=40)


class FlagEnabled(In):
    enabled: bool


class Flag(Out):
    key: str
    app: str
    kind: str
    envs: dict[str, dict[str, Any]]
    description: str | None = None
    variants: list[str] = Field(default_factory=list)


# ---- analytics and logs ---------------------------------------------------------------------------------
class Analytics(Out):
    summary: dict[str, Any]
    retention_days: U
    raw_terms: bool
    enabled: bool
    opted_out: U | None = None
    storage_bytes: U | None = None


class AnalyticsRetention(In):
    days: int = Field(ge=7, le=400)


class RetentionSet(Out):
    ok: bool
    days: U


class AnalyticsTerms(In):
    raw: bool


class TermsSet(Out):
    ok: bool
    raw: bool


class LogStats(Out):
    subscribers: U


# ---- connections ----------------------------------------------------------------------------------------
class ConnectionFields(In):
    description: str | None = Field(default=None, max_length=200)
    base_folder: str | None = None
    allowed_ops: list[Literal["upload", "download", "list", "delete", "move", "share_link"]] | None = None
    max_upload_mb: int | None = Field(default=None, ge=1, le=2048)
    bindings: list[BindingIn] | None = None
    max_link_hours: int | None = Field(default=None, ge=1)
    allow_edit_links: bool | None = None
    anonymous_link_apps: list[str] | None = None
    secret: str | None = Field(default=None, max_length=2048, json_schema_extra=_SECRET)
    tenant_id: str | None = None
    client_id: str | None = None
    site: str | None = None
    drive: str | None = None
    share_scope: Literal["", "organization", "users", "anonymous"] | None = None
    auth: Literal["secret", "certificate"] | None = None
    certificate_b64: str | None = Field(default=None, json_schema_extra=_SECRET)
    certificate_password: str | None = Field(default=None, json_schema_extra=_SECRET)
    endpoint: str | None = None
    bucket: str | None = None
    region: str | None = None
    access_key: str | None = None
    public_url: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = None
    username: str | None = None
    ssl: Literal["disable", "prefer", "require"] | None = None
    readonly: bool | None = None
    max_rows: int | None = Field(default=None, ge=1)
    timeout_s: int | None = Field(default=None, ge=1)


_CONN_KIND = Literal["sharepoint", "s3", "postgres", "mysql", "sqlserver"]


class ConnectionCreate(ConnectionFields):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    kind: _CONN_KIND


class ConnectionUpdate(ConnectionFields):
    kind: _CONN_KIND | None = None


class BindingsSet(In):
    bindings: list[BindingIn]


class ConnectionRotate(In):
    secret: str | None = Field(default=None, max_length=2048, json_schema_extra=_SECRET)
    certificate_b64: str | None = Field(default=None, json_schema_extra=_SECRET)
    certificate_password: str | None = Field(default=None, json_schema_extra=_SECRET)


# ---- operations -----------------------------------------------------------------------------------------
class ContainerLimits(In):
    cpu: float = Field(gt=0)
    memory: str = Field(pattern=r"^[0-9]+(\.[0-9]+)?([KMG]i?|[kmg])?$", description="ex.: 256Mi")
    pids: int = Field(ge=1)


class LimitsResult(Out):
    limits: dict[str, Any]
    applied: str


class ContainerLogs(Out):
    container: str
    items: list[dict[str, Any]]


class ContainerInspect(Out):
    """`docker inspect` of the container (environment variables masked)."""


class DatabaseProvision(Out):
    provisioned: dict[str, Any]
    restart_pending: bool


class Replicas(Out):
    """Live hub replicas and the leader of the background tasks."""


class BackupPolicyIn(In):
    schedule: str = Field(min_length=1, max_length=60, description="ex.: diário 02:00")
    keep: int = Field(ge=1, le=90)
    volumes: list[str] = Field(min_length=1, max_length=8)
    destination: str = Field(min_length=1, max_length=80)
    hooks: bool = False
    envs: list[Env] = Field(default=[Env.producao], min_length=1, max_length=2)
    missed: Literal["executar", "pular"] = "executar"
    jitter_s: int = Field(default=20, ge=0, le=3600)
    tz: str = ""


class BackupPolicy(Out):
    app: str
    schedule: str
    keep: U
    volumes: list[str]
    destination: str


class BackupsAside(Out):
    available: bool
    aside: list[dict[str, Any]]


class AsideResult(Out):
    done: bool
    detail: str


# ---- me -------------------------------------------------------------------------------------------------
class InboxIds(In):
    ids: list[str] | None = Field(default=None, description="Ids da Inbox (i:16); sem ids = todos")


class InboxArchive(In):
    ids: list[str]
    archived: bool = True


class InboxChange(Out):
    marked: U | None = None
    changed: U | None = None


class AccessRequest(In):
    app: str = Field(min_length=1, max_length=40)
    reason: str = Field(default="", max_length=500)


class AccessRequestResult(Out):
    ok: bool
    id: str


MODELS: tuple[type, ...] = (
    UploadLink, Done, RoleCreate, RoleUpdate, PermissionGrant, RoleApps, GrantRevoke, BindingIn, VariableCreate,
    VariableUpdate, SecretRotated, Settings, SettingsUpdate, ApiToken, HealthService, HealthServiceUpdate,
    HealthServiceCreate, HealthCheck, Slo, SloSet, Incident, IncidentCreate, IncidentUpdate, FlagRuleIn,
    FlagRolloutIn, FlagEnvIn, FlagUpdate, FlagCreate, FlagEnabled, Flag, Analytics, AnalyticsRetention,
    RetentionSet, AnalyticsTerms, TermsSet, LogStats, ConnectionCreate, ConnectionUpdate, BindingsSet,
    ConnectionRotate, ContainerLimits, LimitsResult, ContainerLogs, ContainerInspect, DatabaseProvision, Replicas,
    BackupPolicyIn, BackupPolicy, BackupsAside, AsideResult, InboxIds, InboxArchive, InboxChange, AccessRequest,
    AccessRequestResult,
)  # fmt: skip
