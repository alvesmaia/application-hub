# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions: [SemVer](https://semver.org/).
The hub version lives in `platform-py/hub/__init__.py` (= `platform-py/pyproject.toml`) and appears in `GET /healthz`.
Update procedure: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md). New release: `bash platform-py/scripts/release.sh <version>`.
Versions 0.x = pre-1.0: the API may change between minor versions.

## [Unreleased]

### Added
- `install.sh`: optional SharePoint questions for the off-site backup copy (or `SP_*` + `SP_SECRET_FILE` without a
  terminal); creates the `backups-sharepoint` connection with `python -m hub.admin add-sharepoint` (secret via
  stdin into the vault) and sets `HUB_BACKUP_OFFSITE_URL`.
- Off-site backup copy to SharePoint: `HUB_BACKUP_OFFSITE_URL=sharepoint://<connection>/<folder>` sends each
  backup package through an existing SharePoint connection and reads it back checking the sha256.
- `platform-py/scripts/db-tests.sh`: tests for the SQL broker against real PostgreSQL, MySQL and SQL Server in
  disposable containers.
- Webhook audit anchor: `HUB_AUDIT_HEAD_URL` (+ `HUB_AUDIT_HEAD_TOKEN`) receives the signed audit head on each event,
  in the background; failures appear on the Audit screen.
- `hub check --full`: also runs the full contract suite (`platform/conformance`, bundled in the CLI wheel) against the
  app, using the `conformance.json` profile (`hub project create` generates one). A profile without `mutation` skips
  the idempotency/audit tests; `CONF_PROFILE` points to the profile.
- SDK: `hub_sdk.realtime` (`canal`, `sse`): notification channel for SSE/WebSocket with `LISTEN`/`NOTIFY` across
  replicas; the "Tempo real" (Real time) guide now uses it instead of the copied file.
- `hub check`: when the app is not ready, the message includes the last `/_hub/ready` response and explains the case
  of a 204 sent with a body (the server drops the response and the only sign was the timeout).
- Single sign-out with Entra ID: signing out of a session opened through Entra also ends the Microsoft session and
  returns to `/login` (register `<HUB_PUBLIC_URL>/login` as the app's redirect URI).
- Entra ID client secret in the vault: `python -m hub.admin set-entra-secret` (reads standard input by default);
  without `HUB_ENTRA_CLIENT_SECRET(_FILE)`, the hub reads it from the vault.

### Changed
- Codebase in English: comments, docstrings, CLI help/prompts/output, app templates (`hub project create`) and
  documentation. The main README is now English; Portuguese in `README.pt-BR.md`. UI texts stay translated
  (pt-BR/en/es) through the i18n catalogs.
- SDK: `hub_sdk.realtime` renamed to `Channel`/`channel` with `start()`, `close()`, `notify()`, `messages()`. The
  Portuguese names (`Canal`, `canal`, `iniciar`, `fechar`, `avisar`, `mensagens`) still work as deprecated aliases
  (`DeprecationWarning`) and will be removed in the next minor release.

### Validated
- Load (13/10/2026): 1 hub container = 200 simultaneous users; 2 containers behind the edge = 300
  (`docs/IMPLANTACAO.md`). `uvicorn --workers` is not supported (internal broker port).
- MCP login via real Entra ID (13/10/2026): owner's tenant, Claude Code and GitHub Copilot CLI authenticated through
  the browser (hub OAuth 2.1 with SSO session); tokens with `mcp api:write` scopes recorded in the audit log.

### Removed
- Spanish (`es`) language: the hub and the SDK stay in pt-BR and English. Anyone with `es` saved or a Spanish browser
  now sees English; `PUT /api/me/prefs` with `lang: "es"` returns 400.
- Remnants of the previous stack: parity tool (`platform-py/parity`), copies of the old apps in `platform/demo-apps`
  (only `assets/demo.css` and the JS tests' jsdom remain), non-Python SDK examples in the Contract and in the guides;
  the tests' E2E and `hub pack` now use the `app-hub` stack and the Python CLI. Python only.
- In-Hub approval of agent actions in the Inbox (`destructive_actions = "approve_in_hub"`, page
  `/account/agent-approval/{id}`): the default becomes `confirm` — the first destructive MCP call returns a
  `confirm_token` and the second one executes; the AI client is the one that confirms with the person. `approve_in_hub`
  in configuration is now refused at startup (use `confirm` or `deny`). Old agent requests disappear from the Inbox.
- `docs/migracao/` (migration history).

### Added
- Per-user usage (Settings › Per-user usage (Configurações › Uso por usuário), `/admin/usage`): opens, navigations,
  actions, active days and last access per user and per app, with period/user/app filters and CSV. Collected at the
  gateway, requires nothing from the apps; on by default (`HUB_USAGE=0` turns it off), same retention as analytics.
  API `GET /api/v1/usage` (`usage_query`, also in MCP); access with "Edit settings" (Editar configurações) or
  "View audit" (Ver auditoria).
- `install.sh`: interactive mode (no options, in a terminal) — asks for admin, port, bind, public URL, project,
  network range and example apps, with the default in brackets, shows a summary and asks for confirmation; `--yes`
  keeps the non-interactive mode for CI. `--get-compose` downloads the official docker-compose (fixed version, sha256
  checked) into `~/.local/bin`, for RHEL/Rocky without root.
- Declarative real time via SSE: `data-hub-sse` (same message format as `data-hub-ws`, fallback polling, no app
  JavaScript); "Real time" guide with a full channel example (SSE, WebSocket, `LISTEN`/`NOTIFY`).
- Documentation: agent index at `/dev/llms.txt` and `/llms.txt`; CLI, SDK and Contract indexes with a summary and a
  link to the full text; animated active rail in "On this page" (Nesta página); hand tests with `curl` in the
  "First app" guide (Primeiro app).
- Zero-downtime update: edge `app-hub-edge` (Caddy) on `HUB_PORT`, `scripts/hub_update.py` (backup, copy with the new
  version, swap, observation and automatic rollback; `--rollback`), `/readyz` that checks the database and data,
  optional new-version notice on the Health page (Saúde) (`HUB_UPDATE_CHECK=1`). Tested under load: 0 lost requests.

### Attention
- `HUB_PORT` moves from `app-hub-web` to the `app-hub-edge` edge: the first `up -d` of this version has a ~10 s stop.
  Your own proxies in front keep working unchanged (the target is the same port).

### Fixed
- Storage broker: an upload whose input chunks were bigger than the upload blocks (e.g. SharePoint upload
  sessions) lost data and failed with "O corpo terminou antes do tamanho declarado".
- Containers page: a container whose image was removed (e.g. by `docker image prune`) blanked the whole page
  ("Sem conexão" for every app); it now falls back to the image name in the container config.
- `hub_update.py --build` built the code of the folder that created the container, not of the checkout running the
  script; updating from a new clone rebuilt the old code.
- Several replicas on the same volume (zero-downtime update, scaling): `ux.json` (preferences), `usage.json`,
  `analytics.json` and `metrics.json` were rewritten in full by each replica, and one would erase what the other had
  written. Writes now happen under a file lock: preferences are re-read before changing, and telemetry adds to the
  file only what the replica counted since the last write.
- SQL broker: "read and write" connections discarded the write (always rollback, on all three databases) and reported
  success; they now commit. On SQL Server, statements with no rows back (`INSERT`, DDL) failed with "Previous statement
  didn't produce any results"; they now return an empty result.
- Entra ID login for users still holding the temporary password (e.g. the first administrator) fell into the mandatory
  password change, and MCP over OAuth rejected the token; the SSO link now voids a temporary password that was never
  changed.
- `hub_update.py`: on Docker with containerd, `--build` failed at backup because the running image lost its reference
  when it received a new `latest`; it now gets the tag `<proj>-web:pre-update` beforehand (backup, rollback and return
  use the tag). The data-volume backup no longer fails when a file changes during the copy.
- Guided tour on the Home page (Início): Esc closes it with focus anywhere on the screen (before, only inside the tour),
  and there is a Close (Fechar) button (×); both remember the step to resume later.
- MCP 2026-07-28: lists carry `ttlMs` and `cacheScope` (Claude Code refused `tools/list`); the SDK no longer loses the
  types of nested schemas when building tools from the app's OpenAPI.
- Installing without the example apps (`HUB_APPS_JSON=./apps.empty.json`) brought the hub down at boot
  (`Aplicação desconhecida: ativos` (Unknown application: ativos)): initial variable loading now discards app bindings
  outside the catalog.
- `hub deploy`: the hub's refusal comes back with its reason (in the `error` field) — before, a large package only said
  `400`. `hub pack` refuses before sending anything over 20 MB and points to the largest files; the hub reports the
  size and the limit. Limit documented in the publishing guide.
- `hub check`: the `§3 página`/`§3 fragmento` conformance failure shows the status and the last exception from
  `.hub/check-app.log` (and, for `no such column`/`no such table`, the tip to delete `.hub/*.db`).
- Rootless Podman (RHEL/Rocky): apps start — tmpfs `/scratch` with `U` instead of `uid=`/`gid=` (refused by Podman);
  without the `cpu` controller delegated to the user, build and containers run without a CPU limit instead of every
  `RUN` failing; compose socket configurable (`HUB_CONTAINER_SOCKET_HOST`) and `install.sh` detects rootless Podman,
  warns about linger/CPU and records the socket; `HUB_BIND6` avoids the `[::1]` conflict with `--bind 0.0.0.0`;
  `install.sh --clone` uses the public repository. Guide in docs/IMPLANTACAO.md (rootless Podman section).
- `hub dev`: forwards WebSocket; uses the same internal token as the app (no 401 on `/_hub/*`); console in UTF-8.
- CLI templates: `uvicorn[standard]`; formatted even with a 32-character id; clear message for an outdated
  `openapi.json`. `hub check` shows the fix command for lint and formatting.
- Contract §13 aligned with §25 (WebSocket `connect-src`); backup scheduler test no longer depends on the clock.

## [0.2.0] — 2026-10-09

### Added
- Dashboard kit (lightweight BI): SQL connections in Connections (Conexões) — PostgreSQL, MySQL/MariaDB and SQL Server
  (password in the vault, read-only by default, row/time limits, test) — and app queries through the broker
  (`POST /_hub/sql/{conexão}/query`, audit without data).
- SDK: `hub_sdk.sql` (broker in the hub, `.hub/connections.toml` in dev, `from_engine`) and `hub_sdk.dashboards`
  (datasets with cache, filters, KPIs, SVG line/area/bar/pie/donut charts, table, CSV/XLSX export); extra
  `hub-sdk[dashboards]`.
- CLI: `hub project create --template dashboard` (or `--with dashboards`); `hub dev` prepares local SQL connections
  with sample data. Guide `platform/docs/DASHBOARDS.md`; contract §22.1.
- Developer portal at `/developers`: Getting started, CLI, SDK, Guides and Contract, built from versioned Markdown
  (server-side rendering, anchors, copy code, filter, raw Markdown at `/dev/docs/*.md`); generated CLI and SDK
  references (`python -m hub.ops.devdocs`); "Copy prompt" (Copiar prompt) button (`data-hub-copy-prompt`) per language.

### Changed
- `hub`, `hub_sdk` and `hub-cli` unified at 0.2.0 (CLI templates pin `hub-sdk==0.2.0`).

## [0.1.0] — 2026-10-08

First public release (pre-1.0). Hub in Python 3.12 (FastAPI + Jinja2 SSR, asyncpg, PostgreSQL, MinIO, Docker).

### Platform
- Global shell (header + menu) that never goes away; apps composed on the same origin, without iframes and without
  app-owned JavaScript (`data-hub-*` behaviors, `hub-safe.js` sanitizer, per-page CSP, Trusted Types).
- Gateway `/apps/{id}/…` and `/_frag/{id}/…`, signed identity for apps (`x-hub-user`), public base (`x-hub-base`),
  WebSocket through the gateway, mandatory readiness at `/{app}/_hub/ready`.
- Local login with a password policy, sessions, role-based access control (RBAC), personal scoped tokens; single
  sign-on with Microsoft Entra ID (OIDC + PKCE, group/role mapping).
- Administration: users and roles, variables and connections (SharePoint/S3 through the broker — credentials never in
  HTML/API/logs), chained audit with a signed external anchor, vault with master key and rotation.
- Operations: deployments via `.hubapp` packages with image build, blue/green, staging, container supervisor, backups
  with least-privilege credentials and restore, "Provision storage" (Provisionar armazenamento).
- Inbox (approvals, access requests), federated search, Home, onboarding, usage analytics (MAU), health, SLOs, logs and
  observability (OTLP, profile with OTel Collector + Jaeger).
- Public API `/api/v1` (OpenAPI 3.1, `/api/docs`) and MCP `/mcp` with OAuth 2.1 (own authorization server), generated
  from a single operations table.
- i18n pt-BR / en / es; Alvesmaia visual identity (light + dark), PWA, device features (barcode/QR reading with ZXing,
  offline queue, printing).
- Developer kit: `hub-cli` (`hub new|dev|check|pack|mcp|deploy`) and the Python SDK `hub_sdk`, served by the hub itself
  at `/dev/cli/simple/`.

### Sample apps (optional, `demo-apps` profile)
- `ativos` (Asset Management), `fornecedores` (Supplier Network), `servicos` (Service Center), `coletor` (data
  collection by camera/reader).

### Distribution
- Apache-2.0 license (`LICENSE`, `NOTICE`), trademark policy (`TRADEMARKS.md`).
- From-scratch install with `platform-py/scripts/install.sh` (generates secrets, builds everything from source, creates
  the first administrator); project/port/network names configurable for multiple instances on the same host.
- Manual image workflow on GHCR + `docker-compose.ghcr.yml`.
