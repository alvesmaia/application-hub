"""SDK Python (FastAPI) dos apps da Application Hub — espelho do `hub-sdk` em Rust.

O contrato que todo app cumpre (qualquer linguagem) está em
`platform/docs/CONTRATO-APP.md`; este pacote o implementa para FastAPI:

- :mod:`hub_sdk.identity` — identidade injetada pelo hub (`x-hub-user`).
- :mod:`hub_sdk.render` — fragmentos HTML com o visual do shell (Jinja2), avisos, 429,
  downloads e CSV seguro.
- :mod:`hub_sdk.jobs` — tarefas duráveis no Postgres (`hub_jobs`): `SKIP LOCKED`, lease
  + heartbeat, fence por tentativa, backoff com jitter, checkpoint na transação do lote,
  cancelamento, dead-letter e retomada de lease vencido.
- :mod:`hub_sdk.idempotency` — middleware `Idempotency-Key` (escopo usuário + rota, hash
  do corpo inteiro inclusive em streaming, repetição, 409/422).
- :mod:`hub_sdk.outbox` — outbox transacional (`hub_outbox`) e evento de auditoria.
- :mod:`hub_sdk.internal` — endpoints `/{app}/_hub/*` com token interno.
- :mod:`hub_sdk.shutdown` — desligamento gracioso (lifespan + SIGTERM).
- :mod:`hub_sdk.heavy` — semáforo de operações pesadas (429 + `Retry-After`).
- :mod:`hub_sdk.app` — :class:`HubSdk`: tudo acima ligado a um app FastAPI.

As tabelas (``SCHEMA_SQL``) são as mesmas do SDK Rust: um app pode trocar de stack sem
migrar dados de tarefas/idempotência/outbox.
"""

from hub_sdk.schema import APPS_PREFIX, OUTBOX_RETENTION, SCHEMA_SQL, base, cleanup, ct_eq, new_id

__all__ = ["APPS_PREFIX", "OUTBOX_RETENTION", "SCHEMA_SQL", "base", "cleanup", "ct_eq", "new_id"]
__version__ = "0.2.0"
