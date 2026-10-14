"""Public API v1 components."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field

EXAMPLES: dict[str, Any] = json.loads((Path(__file__).parent / "examples.json").read_text(encoding="utf-8"))


U = Annotated[int, Field(ge=0)]  # unsigned integer (u32/u64)


class Out(BaseModel):
    """Response: new fields on the internal routes do not break clients."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class In(BaseModel):
    """Request body: closed."""

    model_config = ConfigDict(extra="forbid")


class Env(StrEnum):
    producao = "producao"
    staging = "staging"


class Problem(Out):
    """Error in RFC 9457 format (`application/problem+json`)."""

    type: str = Field(json_schema_extra={"format": "uri-reference"})
    title: str
    status: int = Field(ge=400, le=599)
    detail: str | None = None
    instance: str | None = None


class Page(Out):
    """Page of a list (`items` typed per operation)."""

    items: list[dict[str, Any]]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    next: str | None = None


class AppApi(Out):
    enabled: bool
    base: str | None = None
    openapi: str | None = None


class AppMcp(Out):
    enabled: bool
    exposed_by_hub: bool | None = None
    tool_prefix: str | None = None


class App(Out):
    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str
    version: str
    mode: str
    url: str
    routes: list[str]
    stack: str | None = None
    status: str
    jobs: list[dict[str, Any]]
    api: AppApi
    mcp: AppMcp


class Window(Out):
    availability: float | None = Field(default=None, ge=0, le=1)
    errors: U
    requests: U
    probes: U
    probes_ok: U
    p95: float | None = None
    from_: int = Field(alias="from")
    to: int


class AppStatus(Out):
    app: str
    version: str
    status: str
    open_incidents: list[dict[str, Any]]
    slos: list[dict[str, Any]]
    last_hour: Window


class StatusApp(Out):
    id: str
    name: str
    status: str


class StatusPage(Out):
    overall: str
    apps: list[StatusApp]
    open_incidents: list[dict[str, Any]]


class ObsItem(Out):
    app: str
    env: Env
    availability: float | None = None
    errors: U
    requests: U
    probes: U
    probes_ok: U
    p95_ms: float | None = None
    minutes: U


class Observability(Out):
    minutes: U
    items: list[ObsItem]


class LogLine(Out):
    app: str
    at: U
    level: str
    message: str
    source: str | None = None
    time: str | None = None
    user: str | None = None


class Deployment(Out):
    id: U
    app: str
    env: str
    version: str
    status: str
    at: U
    commit: str | None = None
    origin: str | None = None
    image: str | None = None
    package: str | None = None
    runtime: str | None = None
    sha256: str | None = None
    note: str | None = None


class Job(Out):
    """Asynchronous hub job (deployment, backup, restore)."""

    id: int = Field(ge=0)
    kind: str
    title: str
    steps: list[dict[str, Any]]
    done: bool
    ok: bool
    message: str
    progress: int = Field(ge=0, le=100)
    result: dict[str, Any] | None
    app: str | None = None
    env: Env | None = None


class ContainerAction(Out):
    container: str
    action: str


class Container(Out):
    id: str
    name: str
    env: Env
    container: str
    image: str
    cpu: float
    memory: str
    memory_bytes: U
    pids: U
    restart_pending: bool


class Backup(Out):
    id: U
    app: str
    env: str | None = None
    status: str


class RotateResult(Out):
    rotated: U
    failed: U
    items: list[str]


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    dead = "dead"
    cancelled = "cancelled"


class SagaStep(Out):
    name: str
    state: str


class Saga(Out):
    state: str
    steps: list[SagaStep]


class Task(Out):
    app: str
    app_name: str
    env: Env
    id: str
    kind: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    progress_done: int
    progress_total: int
    progress_text: str
    error: str | None = None
    cancel_requested: bool
    user: str
    created_at: int
    updated_at: int
    elapsed_s: float
    run_after: int
    saga: Saga | None = None


class TaskActionResult(Out):
    job: dict[str, Any]


class Binding(Out):
    app: str
    env: Env


class VarKind(StrEnum):
    publica = "publica"
    secreta = "secreta"


class Variable(Out):
    name: str = Field(pattern=r"^[A-Z0-9_]{1,80}$")
    kind: VarKind
    value: str
    managed: bool
    bindings: list[Binding]


class Connection(Out):
    name: str
    kind: str
    bindings: list[Binding]


class ConnectionTest(Out):
    ok: bool
    steps: list[dict[str, Any]]


class Storage(Out):
    app: str
    env: Env
    bucket: str | None = None
    connection: str | None = None


class StorageChange(Out):
    changed: bool | None = None
    detail: str | None = None
    storage: Storage | None = None


class DeprovisionRequest(In):
    confirm: str = Field(min_length=1, description="Nome do bucket (confirmação).")
    force: bool = False
    password: str | None = Field(default=None, json_schema_extra={"format": "password", "writeOnly": True})


class User(Out):
    id: str
    email: str = Field(json_schema_extra={"format": "email"})
    name: str
    roles: list[str]
    active: bool
    must_change_password: bool
    locked: bool
    locked_until: U | None = None
    failed_attempts: U
    last_login: U | None = None
    created_at: U
    sso: bool
    local_password: bool


class UserCreate(In):
    email: str = Field(json_schema_extra={"format": "email"})
    name: str = Field(min_length=1, max_length=120)
    roles: list[str]
    password: str = Field(min_length=12, json_schema_extra={"format": "password", "writeOnly": True})
    must_change_password: bool | None = None


class UserUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    roles: list[str] | None = None
    active: bool | None = None


class Role(Out):
    name: str
    description: str
    level: str
    members: U
    permissions: list[bool] = Field(min_length=6, max_length=6)


class AuditEntry(Out):
    id: U
    at: U
    actor: str
    scope: str
    title: str
    detail: str
    icon: str | None = None


class UsageRow(Out):
    user: str
    name: str
    email: str
    app: str
    app_name: str
    opens: U
    sections: U
    actions: U
    days: U
    last: U


class AuditVerify(Out):
    ok: bool
    entries: U
    segments: U
    checkpoints: U
    problems: list[dict[str, Any]]
    last_seq: U
    last_hash: str | None = None


class InboxItem(Out):
    id: str
    kind: str
    title: str


class Decision(StrEnum):
    approve = "approve"
    reject = "reject"


class DecideRequest(In):
    decision: Decision
    comment: str | None = Field(default=None, max_length=500)


class DecideResult(Out):
    ok: bool
    status: str | None = None


class SearchHit(Out):
    kind: str
    title: str
    subtitle: str
    url: str
    score: float


class SearchGroup(Out):
    id: str
    label: str
    hits: list[SearchHit]


class SearchResult(Out):
    q: str
    total: U
    took_ms: U
    partial: bool
    unavailable: list[str]
    groups: list[SearchGroup]


MODELS: tuple[type, ...] = (
    Env, Problem, Page, AppApi, AppMcp, App, Window, AppStatus, StatusApp, StatusPage, ObsItem,
    Observability, LogLine, Deployment, Job, ContainerAction, Container, Backup, RotateResult,
    TaskStatus, SagaStep, Saga, Task, TaskActionResult, Binding, VarKind, Variable,
    Connection, ConnectionTest, Storage, StorageChange, DeprovisionRequest, User, UserCreate,
    UserUpdate, Role, AuditEntry, AuditVerify, UsageRow, InboxItem, Decision, DecideRequest, DecideResult,
    SearchHit, SearchGroup, SearchResult,
)  # fmt: skip

_NULL = {"type": "null"}


def _clean(v: Any) -> Any:
    """Pydantic JSON Schema for the utoipa shape (no `title`; `anyOf` with null becomes nullable)."""
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if not isinstance(v, dict):
        return v
    out = {k: _clean(x) for k, x in v.items() if k not in ("title", "default")}
    if out.get("additionalProperties") is True:
        del out["additionalProperties"]
    alts = out.get("anyOf")
    if alts and _NULL in alts:
        rest = [a for a in alts if a != _NULL]
        del out["anyOf"]
        if len(rest) == 1 and "$ref" not in rest[0] and isinstance(rest[0].get("type"), str):
            out |= rest[0] | {"type": [rest[0]["type"], "null"]}
        else:
            out["oneOf"] = [*rest, _NULL]
    return out


def schemas() -> dict[str, Any]:
    """All components (`#/components/schemas/<Name>`), with `examples` when present."""
    out: dict[str, Any] = {}
    for m in MODELS:
        if issubclass(m, StrEnum):
            s: dict[str, Any] = {"type": "string", "enum": [e.value for e in m]}
        else:
            raw = cast(Any, m).model_json_schema(by_alias=True, ref_template="#/components/schemas/{model}")
            raw.pop("$defs", None)
            s = _clean(raw)
            s["additionalProperties"] = cast(Any, m).model_config.get("extra") != "forbid"
            if m.__doc__:
                s["description"] = m.__doc__.strip()
        if m.__name__ in EXAMPLES:
            s["examples"] = [EXAMPLES[m.__name__]]
        out[m.__name__] = s
    return out


def inline(name: str) -> dict[str, Any]:
    """Schema without `$ref` (the `inputSchema` of the MCP tools)."""
    all_ = schemas()

    def walk(v: Any, depth: int = 0) -> Any:
        if isinstance(v, dict):
            if isinstance(r := v.get("$ref"), str):
                return walk(all_[r.rsplit("/", 1)[-1]], depth + 1) if depth < 8 else {}
            return {k: walk(x, depth) for k, x in v.items() if k != "examples"}
        if isinstance(v, list):
            return [walk(x, depth) for x in v]
        return v

    return walk(all_[name]) if name in all_ else {}


def example(name: str) -> Any:
    return EXAMPLES.get(name)
