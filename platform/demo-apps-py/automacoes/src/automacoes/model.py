"""Packages → processes → triggers → runs: statuses, the run state machine, origins, the durable job
type, the row types and the database schema."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from hub_sdk.jobs import JobSpec

from automacoes.fields import FieldSpec
from automacoes.present import humanize

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

T = TypeVar("T", bound="DataclassInstance")

PENDING, PROCESSING, DONE, ERROR, CANCELLED = "pendente", "processando", "concluido", "erro", "cancelado"
STATUSES = (PENDING, PROCESSING, DONE, ERROR, CANCELLED)
TERMINAL = frozenset({DONE, ERROR, CANCELLED})
# A terminal status has no way out: late job outcomes (a cancelled run that finishes anyway,
# a retry after "erro") can never rewrite what the user already saw.
TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({PROCESSING, ERROR, CANCELLED}),
    PROCESSING: frozenset({DONE, ERROR, CANCELLED}),
}
STATUS_LABELS = {
    PENDING: "Pendente",
    PROCESSING: "Processando",
    DONE: "Concluído",
    ERROR: "Erro",
    CANCELLED: "Cancelado",
}
STATUS_CLASS = {
    PENDING: "status-draft",
    PROCESSING: "status-pending",
    DONE: "status-active",
    ERROR: "status-rejected",
    CANCELLED: "status-draft",
}
TRIGGERS = ("manual", "agendamento", "api")
TRIGGER_LABELS = {"manual": "Manual", "agendamento": "Gatilho", "api": "API"}
SCHEDULER_USER = "agendamento"

DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S = 300, 3600
JOB_KIND = "automacoes.run"
# One attempt is bounded by the process timeout (≤ 3600 s) plus extraction and bookkeeping.
JOB_SPEC = JobSpec(JOB_KIND, max_attempts=3, max_duration=float(MAX_TIMEOUT_S + 60))


def can_move(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())


def sources(dst: str) -> list[str]:
    """Statuses a run may be in to move to ``dst`` (the ``WHERE`` of the idempotent update)."""
    return [s for s, targets in TRANSITIONS.items() if dst in targets]


def row_to(cls: type[T], r: Any) -> T:
    return cls(**{f.name: r[f.name] for f in fields(cls)})


@dataclass(frozen=True)
class Package:
    id: int
    name: str
    version: str
    description: str
    entrypoint: str
    fields: list[dict[str, Any]]
    labels: dict[str, str]
    sha256: str
    size: int
    uploaded_by: str
    created_at: datetime

    @property
    def specs(self) -> tuple[FieldSpec, ...]:
        return tuple(FieldSpec.model_validate(f) for f in self.fields)

    def label_for(self, key: str) -> str:
        f = next((f for f in self.fields if f.get("name") == key), None)
        return str(f["label"]) if f is not None else self.labels.get(key) or humanize(key)


PACKAGE_COLS = (
    "id, name, version, description, entrypoint, fields, labels, sha256, size, uploaded_by, created_at"
)


@dataclass(frozen=True)
class Process:
    id: int
    name: str
    package_name: str
    package_version: str
    params: dict[str, Any]
    timeout_s: int
    enabled: bool
    created_by: str


PROCESS_COLS = "id, name, package_name, package_version, params, timeout_s, enabled, created_by"


@dataclass(frozen=True)
class Run:
    id: int
    process_id: int | None
    process_name: str
    package_name: str
    package_version: str
    params: dict[str, Any]
    status: str
    trigger: str
    requested_by: str
    requested_login: str
    trigger_id: int | None
    job_id: str | None
    timeout_s: int
    output: dict[str, Any] | None
    error: str | None
    log: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


RUN_COLS = (
    "id, process_id, process_name, package_name, package_version, params, status, trigger, requested_by, "
    "requested_login, trigger_id, job_id, timeout_s, output, error, log, created_at, started_at, finished_at, "
    "duration_ms"
)


@dataclass(frozen=True)
class Trigger:
    id: int
    process_id: int
    process_name: str
    params: dict[str, Any]
    kind: str
    every_minutes: int | None
    at_time: str | None
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_run_id: int | None
    last_status: str | None
    created_by: str


TRIGGER_COLS = (
    "g.id, g.process_id, p.name AS process_name, g.params, g.kind, g.every_minutes, g.at_time, g.enabled, "
    "g.next_run_at, g.last_run_at, g.last_run_id, r.status AS last_status, g.created_by"
)
TRIGGER_FROM = (
    "aut_triggers g JOIN aut_processes p ON p.id = g.process_id LEFT JOIN aut_runs r ON r.id = g.last_run_id"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aut_packages (
  id bigserial PRIMARY KEY,
  name text NOT NULL,
  version text NOT NULL,
  description text NOT NULL DEFAULT '',
  entrypoint text NOT NULL,
  fields jsonb NOT NULL DEFAULT '[]',
  labels jsonb NOT NULL DEFAULT '{}',
  sha256 text NOT NULL,
  size integer NOT NULL,
  content bytea NOT NULL,
  uploaded_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (name, version)
);
CREATE TABLE IF NOT EXISTS aut_processes (
  id bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  package_name text NOT NULL,
  package_version text NOT NULL,
  params jsonb NOT NULL DEFAULT '{}',
  timeout_s integer NOT NULL DEFAULT 300 CHECK (timeout_s BETWEEN 1 AND 3600),
  enabled boolean NOT NULL DEFAULT true,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (package_name, package_version) REFERENCES aut_packages (name, version)
);
CREATE TABLE IF NOT EXISTS aut_runs (
  id bigserial PRIMARY KEY,
  process_id bigint,
  process_name text NOT NULL,
  package_name text NOT NULL,
  package_version text NOT NULL,
  params jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'pendente'
    CHECK (status IN ('pendente','processando','concluido','erro','cancelado')),
  trigger text NOT NULL CHECK (trigger IN ('manual','agendamento','api')),
  requested_by text NOT NULL,
  requested_login text NOT NULL,
  trigger_id bigint,
  job_id text,
  timeout_s integer NOT NULL DEFAULT 300,
  output jsonb,
  error text,
  log text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  duration_ms integer
);
CREATE INDEX IF NOT EXISTS aut_runs_process ON aut_runs (process_id, id DESC);
CREATE INDEX IF NOT EXISTS aut_runs_login ON aut_runs (requested_login, id DESC);
CREATE INDEX IF NOT EXISTS aut_runs_created ON aut_runs (created_at);
CREATE TABLE IF NOT EXISTS aut_triggers (
  id bigserial PRIMARY KEY,
  process_id bigint NOT NULL REFERENCES aut_processes (id) ON DELETE CASCADE,
  params jsonb NOT NULL DEFAULT '{}',
  kind text NOT NULL CHECK (kind IN ('intervalo','diario')),
  every_minutes integer CHECK (every_minutes >= 5),
  at_time text,
  enabled boolean NOT NULL DEFAULT false,
  next_run_at timestamptz NOT NULL,
  last_run_at timestamptz,
  last_run_id bigint,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS aut_triggers_due ON aut_triggers (next_run_at) WHERE enabled;
"""
