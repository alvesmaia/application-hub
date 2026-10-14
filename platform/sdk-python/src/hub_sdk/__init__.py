"""Python SDK (FastAPI) for the Application Hub apps.

The contract every app complies with (any language) is in
`platform/docs/CONTRATO-APP.md`; this package implements it for FastAPI:

- :mod:`hub_sdk.identity` — identity injected by the hub (`x-hub-user`).
- :mod:`hub_sdk.render` — HTML fragments with the shell's look (Jinja2), notices, 429,
  downloads e CSV seguro.
- :mod:`hub_sdk.jobs` — durable jobs in Postgres (`hub_jobs`): `SKIP LOCKED`, lease
  + heartbeat, per-attempt fence, backoff with jitter, checkpoint in the batch transaction,
  cancellation, dead-letter and resumption of an expired lease.
- :mod:`hub_sdk.idempotency` — `Idempotency-Key` middleware (user + route scope, hash
  of the whole body, including streaming, replay, 409/422).
- :mod:`hub_sdk.outbox` — transactional outbox (`hub_outbox`) and audit event.
- :mod:`hub_sdk.internal` — `/{app}/_hub/*` endpoints with an internal token.
- :mod:`hub_sdk.shutdown` — desligamento gracioso (lifespan + SIGTERM).
- :mod:`hub_sdk.heavy` — semaphore for heavy operations (429 + `Retry-After`).
- :mod:`hub_sdk.app` — :class:`HubSdk`: all of the above wired to a FastAPI app.

The tables (``SCHEMA_SQL``) have a stable format (data already written): do not change without a migration.
"""

from hub_sdk.schema import APPS_PREFIX, OUTBOX_RETENTION, SCHEMA_SQL, base, cleanup, ct_eq, new_id

__all__ = ["APPS_PREFIX", "OUTBOX_RETENTION", "SCHEMA_SQL", "base", "cleanup", "ct_eq", "new_id"]
__version__ = "0.2.0"
