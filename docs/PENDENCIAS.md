# Pending items — Python stack (updated 09/10/2026)

Living list: update it with every delivery. In-progress tasks live in the agent's
native task list.

## Status

- **Python hub** (`platform-py/hub`) in production (the owner's instance) since 07/10/2026 (F16: production
  configuration, demo accounts disabled). Waves 1–13: gateway, vault, state, login/RBAC,
  Administration, Operations (blue/green, staging, image build, backups), `/api/v1` API + MCP + OAuth 2.1, Inbox,
  search, i18n (pt-BR/en), observability, per-user usage (`/admin/usage`, collected at the gateway; `HUB_USAGE=0`
  turns it off).
- **Official apps** in Python: `ativos`, `fornecedores`, `servicos`, `coletor` (`platform/demo-apps-py/*`), SDK
  `hub_sdk` (`platform/sdk-python`). **Python only** (since 13/10/2026 with no traces of the previous stack);
  other stacks: to be re-evaluated in a future expansion. Languages: pt-BR and en (`es` removed on 13/10/2026).
- **Developer kit** (F22): `hub-cli` (`hub new|dev|check|pack|mcp`) served at `/dev/cli/simple/`.
- Shared front-end in `platform/assets/{shell,web,i18n}` and app registry in `platform/config/`.

- **Distribution 0.1.0** (task #23): Apache-2.0 (`LICENSE`, `NOTICE`, `TRADEMARKS.md`), fresh install
  `platform-py/scripts/install.sh`, optional sample apps (`demo-apps` profile), configurable names/port/networks
  (`HUB_PROJECT`, `HUB_PORT`, `HUB_SUBNET`), version in `/healthz`, `CHANGELOG.md`, `docs/ATUALIZACAO.md`,
  `scripts/release.sh`, manual GHCR image workflow.

## Open

### Other

- **Zero-downtime update:** in use on the owner's instance since 13/10/2026 (`hub_update.py --build`, 0 failures
  in 194 `/healthz` checks during the switch). State across replicas on the same volume resolved on 13/10/2026 (UX, usage, analytics and metrics now join
  the file under a lock).
- **Repository:** since 14/10/2026 the public `alvesmaia/application-hub` is the only one; the old private one (full
  history and the Rust stack) was archived as `alvesmaia/application-hub-py`.
- Load: measured on 13/10/2026 (1 container = 200 users; table in `docs/IMPLANTACAO.md`). Hub replicas in compose and
  `uvicorn --workers` are not pursued (decided 15/10/2026).

## Future ideas

- **Multiple servers under one hub:** a single hub manages the Docker of several servers and places/moves apps
  between them, so the platform scales by adding servers (instead of hub replicas). Today the hub drives only its own
  host's Docker (`hub/ops`, guard `docker_guard.py`).
- **English i18n keys:** the catalogs still use the pt-BR text as the key (`t()`/`tr()`); switching keys to English
  touches every call and template (~4,900 entries). Code, comments and docs are English since 14/10/2026.
- **English ids for the sample apps** (`ativos`, `fornecedores`, `servicos`, `coletor`): needs database/data migration and
  breaks existing URLs and MCP tool names.
- **ODBC in the SQL broker:** out of the roadmap as of 14/10/2026. Postgres, MySQL and SQL Server already have native
  connections; ODBC would only make sense with the driver of a specific database (Oracle, DB2…) inside the hub image.
- **Human approval of agent actions in the hub:** removed on 13/10/2026 (the MCP default became `confirm`; the
  confirmation stays with the AI client). If it comes back: an "Approve agent action" request in the Inbox with an
  **Authorize** button + confirmation modal (no password), approvable only through the browser session (the agent token
  never approves), and the `confirm_token` deadline renewed from the approval — in the old design the 5 min counted
  from the request and the person would exceed the deadline before telling the agent. Previous implementation in the git
  history (`e33ae8d`).
