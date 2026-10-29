# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions: [SemVer](https://semver.org/).
The hub version lives in `platform-py/hub/__init__.py` (= `platform-py/pyproject.toml`) and appears in `GET /healthz`.
Update procedure: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md). New release: `bash platform-py/scripts/release.sh <version>`.
Versions 0.x = pre-1.0: the API may change between minor versions.

## [Unreleased]

### Attention
- **One Postgres only:** the hub's `hub` database now lives in the apps' server (`app-hub-app-db`); the compose no
  longer has `app-hub-db` and `APP_HUB_DB_PASSWORD` is no longer used. An existing install must move its hub database
  once with `scripts/db-merge.sh` (dump, restore into an empty target, row counts compared, old server kept stopped
  for rollback): see «Moving to the single Postgres» in `docs/ATUALIZACAO.md`. Until then `install.sh`, `hub-start.sh`,
  `dev.sh` and `hub_update.py` stop with that instruction instead of starting an empty hub.
- New secrets `APP_HUB_APP_DB_PASSWORD_<APP>` (the sample apps' own roles): `install.sh --config-only --yes` on an
  existing `.prod.env` generates only what is missing.

### Added
- Per-app limits on the database role: `statement_timeout`, `idle_in_transaction_session_timeout` and
  `temp_file_limit` (`HUB_APP_DB_*`), applied when provisioning and refreshed on an already provisioned app. The
  Postgres keeps 30 connections for the hub (`superuser_reserved_connections`, `max_connections=200`).
- `install.sh` rerun: secrets added to `prod.env.example` in later versions are generated into the existing
  `.prod.env` (names shown, values never). The install notice says there is one Postgres.

### Security
- The sample apps no longer connect as the superuser `hub`: each has its own role, owner of its database
  (`app-hub-db-roles` job, `python -m hub.admin demo-db-roles`; an existing install's tables are handed over), and the
  hub moves the seeded superuser `DATABASE_URL_<APP>` variables to the role at boot.
- The hub revokes `PUBLIC` access to its own database at boot, and app backups, restores and staging clones refuse the
  `hub` database.

### Fixed
- Login looped back to `/login` on an `http://` install other than localhost: `docker-compose.prod.yml` fixed the
  session cookie as Secure. It now reads `HUB_COOKIE_SECURE` from `.prod.env`, and `install.sh` writes `auto` for an
  `http://` URL (with a warning that it is for tests only).
- Behind a corporate proxy, the hub's internal calls (apps, MinIO) followed the `HTTP_PROXY` docker injects into
  containers and failed at the proxy ("Access Denied"). See *Added* (hub.httpclients).
- `hub_update.py`: the `/data` backup now uses `--volumes-from`, so a host-folder `/data` is backed up too; the
  edge health probe also works over HTTPS (an update no longer rolls back on a TLS edge).
- `install.sh`: the sample bucket loop no longer retries 60 times in silence (it shows the error and the likely
  cause once); the final message gives absolute paths; a rerun with an existing `.prod.env` asks nothing again.
- MCP OAuth: a dynamically registered client that did not finish the sign-in within one hour was deleted, and every
  later attempt with the cached `client_id` (mcp-remote, Claude, Copilot) failed with "client_id desconhecido";
  unused registrations now last 30 days (still capped by the unused-client limit and the registration rate limits).
- MCP OAuth: a client metadata document listing a private-use redirect scheme (GitHub Copilot desktop:
  `github-app://oauth/callback`) was refused whole; unsupported redirect URIs are now dropped and the usable
  loopback/HTTPS ones kept.

### Changed
- `install.sh` with options but no `--yes` now asks for consent; without a terminal it requires `--yes`.
- The edge mounts `infra/edge` (Caddyfile + `plain.caddy`/`tls.caddy`) and `/certs`: the next full `up -d` recreates
  `app-hub-edge` once (a few seconds).
- Deployments › Import package: the main flow is choose the `.hubapp` → preview → variables/secrets → environment →
  deploy. The button states its effect ("Implantar em staging" / "Enviar para aprovação (produção)"). The manifest
  validator moved, collapsed and empty (the example is only a placeholder), to the "Pipeline e CI" tab: the
  deployment always used the manifest inside the package. Empty `config` values are dropped (= keep the hub's value).
  Step-by-step manual import in `platform/docs/dev/guias/publicar.md` (and pointers in the other docs).
- Developer docs: the agent instructions (and the copy-prompt button) are served at `/dev/docs/AGENTS.md`, the app
  rules at `/dev/docs/AGENTS-app.md`; the old `agente.md`/`agents.md` answer 308.

### Added
- Install on corporate networks (`docs/REDE-CORPORATIVA.md`): `install.sh --proxy`, `--no-proxy`, `--ca-file`,
  `--pypi-index`, `--registry-mirror`, `--apk-mirror`, `--goproxy` (also asked interactively), passed to every build
  (Dockerfiles of the hub, MinIO, sample apps and the `hub-cli` template take `IMAGE_PREFIX` and `EXTRA_CA_PEM`).
- `install.sh --doctor`: read-only diagnosis (network through the configured path only, TLS inspection with the
  certificate issuer, CA, mirrors, a build inside a container, disks, port, other projects on the engine); it is also
  step 0 of the install (`--skip-doctor`).
- `install.sh`: notice and consent before acting (`Continuar? (s/N)`; `--yes` = consent, required without a terminal).
- `install.sh`: disk choice (writable disks with free space; `--data-dir` for databases, hub data and MinIO files;
  `--engine-store` for the images on rootless Podman), HTTPS at the edge (`--tls-cert`/`--tls-key`, `tls.caddy`),
  rootless Podman with or without a systemd user session (`--no-systemd`, `--autostart cron|systemd|none`,
  `scripts/hub-start.sh`), and `.secrets/install-cmd.txt` with this install as one command.
- `hub.httpclients`: internal calls are always direct; external ones use `HUB_PROXY` (or `*_PROXY`) except for
  `HUB_NO_PROXY`, hosts without a dot and private addresses, and trust the corporate CA mounted at `/config/ca`.
  Apps built and run by the hub get the proxy, the CA and the mirrors (build args and container environment).
- `POST /api/ops/packages/preview` (multipart `package`, `X-Hub-Request: 1`, same permission and token action as the
  upload): reads only `manifest.json` from the package and returns id, name, version, new app or the current version
  per environment, and `requires` (names only). The Import tab shows it before deploying, with one field per
  `requires.variables`/`requires.secrets` (secrets as password fields) sent as `config`, like `hub deploy`.
- MCP and `/api/v1`: every remaining write action of the API (and the reads that go with them) as tools, with the
  same RBAC as the screens and a `confirm_token` round-trip for the destructive ones: users (delete, unlock; create
  and update now exposed, update counts as destructive), roles (create, update, delete, permissions, apps), access
  grants, variables (create, update, delete, rotate token/database password), settings, API tokens (list, revoke,
  delete), health services, SLOs, incidents, feature flags, analytics (retention, terms, key rotation), log stats,
  connections (create, update, delete, bindings, credential and storage-token rotation), container limits/apply/
  logs/inspect, database provisioning, replicas, backup policy, clone/test, set-aside databases, maintenance
  release, and the personal Inbox read/archive and access requests. JSON bodies are validated against their
  Pydantic schema at the edge (400 naming the field); secrets are write-only and never come back.
- MCP tool and API op `deployments_upload` (`POST /api/v1/deployments/upload-link?env=staging|producao[&commit=]`):
  returns a one-time upload link (`<public base>{mcp.path}/uploads/<ticket>`, 10 minutes, bound to who asked) and a
  ready `curl -F package=@…` command, so an agent behind Cloudflare Access can publish a `.hubapp`. The upload needs
  no session or bearer (the ticket is the credential, stored hashed, spent atomically), re-checks that the owner is
  active and still may upload packages (and the token's app scope), keeps the admin-network rule, is rate limited
  per IP and runs the same import as `POST /api/ops/packages` (`?wait=true` waits; production still waits for
  approval). `POST /api/v1/deployments` also accepts `?wait=true`.
- GitHub icon in the global header (new tab, pt-BR/en label) pointing to `HUB_SOURCE_URL` (default
  `https://github.com/alvesmaia/application-hub`; empty or non-`https://` hides it).
- `install.sh`: "Revisar opções avançadas?" with every optional setting (MinIO folder, audit anchor folder and
  webhook, off-site copy none/SharePoint/S3, Entra ID sign-in, admin CIDRs, proxy, telemetry, update notice,
  public docs, build CPUs); the recommended value is the default and Enter keeps it. `--config-only` only writes
  `.prod.env`; `HUB_OBJSTORE_DIR` puts the MinIO data on a host folder.
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
- Code size rules (AGENTS.md "Code size and clean code"), enforced by `hub-check` in every project: ruff `C901`
  (complexity > 8), `PLR0912` (> 12 branches), `PLR0915` (> 20 statements), `PLR1702` (> 3 nested blocks, via
  explicit preview) and a file-size ratchet (no code file over 500 lines, `platform-py/size-baseline.json`). The
  whole codebase was refactored to fit, without behavior changes (page HTML, OpenAPI, MCP tools and SDK public
  API checked identical): routes are module-level routers with the shared state as a FastAPI dependency (no more
  routes as closures inside `mount_*`), long work in `*_jobs.py`, page rows in pure `*_view.py`; the shell JS files
  are split by responsibility (e.g. `hub-device-camera.js`, `hub-kit-mount.js`, `hub-nav.js`).

### Fixed
- SDK `openapi_of`: no more "Duplicate Operation ID" warnings in the app logs for page routes that answer
  several methods; duplicate operation ids among `/api/` routes now fail with the ids listed.
- Backups: the database dump no longer carries privileges (`pg_dump --no-privileges`). It held production's grants
  (`ALTER DEFAULT PRIVILEGES FOR ROLE <app role>`), so a clone to staging (or a restore) failed with "permission denied
  to change default privileges" when the database admin is not a superuser. The backup role's grants are applied
  again on every backup.
- Scheduled backups: the apps are backed up at the same time and every run copied its SQL to the same
  `/tmp/hub-role.sql` in the apps database container, so most apps failed each night (`HUB-WRONG-DB` or
  `No such file`). Each `psql -f` file (role, admin, restore) now has its own name.
- SDK: `openapi_of` missed routes added with `include_router` (FastAPI wraps them in one route); the apps no
  longer copy routes one by one.
- Sample apps (ativos, fornecedores, servicos): links and forms follow the public base from `x-hub-base`
  (`hub_sdk.render.public_base`; `Renderer.page` passes it as `base`) instead of a fixed `/apps/<id>`.
- `dash-iso-proof.py`: the usage named `bind` (the command is `link`), and an unknown command silently ran `check`.
- Containers page showed "Em execução" right after Stop: a container read already in flight when an action landed
  was served and cached (`rt.Engine` now drops it).
- Several replicas: `auth.json` was written after the cross-replica lock was released, so a concurrent update
  could be lost; the write now happens inside the lock.
- `install.sh`: a `#` or `&` in the SharePoint folder broke the `sed` that writes `HUB_BACKUP_OFFSITE_URL`.
- Blue/green rollout kept the replaced container's network aliases off the candidate: with upstreams that point to
  an alias (installer/compose names), the app answered 502 after the next hub restart.

### Validated
- Heavy validation after the refactor (15/10/2026, isolated instance): full E2E 148 passed; full ui-sweep (1,918
  visits) with no finding; DR, zero downtime and chaos OK (state database restart: hub back in 1.2 s).
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
