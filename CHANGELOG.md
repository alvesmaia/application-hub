# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions: [SemVer](https://semver.org/).
The hub version lives in `platform-py/hub/__init__.py` (= `platform-py/pyproject.toml`) and appears in `GET /healthz`.
Update procedure: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md). New release: `bash platform-py/scripts/release.sh <version>`.
Versions 0.x = pre-1.0: the API may change between minor versions.

## [Unreleased]

### Changed
- Sample apps' databases and roles are `app_<id>`, like any app (they were the bare ids). Existing installs move
  by themselves on the update: the `app-hub-db-roles` job renames database, role and read group and updates the read
  accounts (data kept); `hub_update.py` runs it before recreating the apps; the hub rewrites the stored connections
  at boot. SQL clients: use `app_<id>` as the database.
- Application catalog cards no longer show the operational state ("Operacional/Degradado"); it stays in Status.
- Database access screen without explanatory texts: no "external access on/off" lines, no copy in the new-account and
  credentials dialogs, no hint under "Banco do hub".

## [0.7.0] — 2026-11-26

### Removed
- Search: the hub no longer has a global search — no Ctrl+K palette, no `/search` page, no `GET/POST /api/me/search`,
  no `search` operation in `/api/v1` and MCP, no "Use the search" step in the tutorial and tour. The usage analytics
  no longer count searches nor keep terms without results (`PUT /api/v1/analytics/terms` is gone; data saved by
  older versions drops those fields on load). Apps: the hub no longer calls `GET /{app}/_hub/search`; the SDK drops
  `sdk.search`/`hub_sdk.search` and the sample apps their search providers. Each app keeps its own list filters.

### Fixed
- Tutorial: the "notifications" step pointed to `/inbox#prefs`, where there is no form, so it could not be
  completed; it now leads to My account › Preferences (`/account#prefs`), where saving the channels completes it.

### Added
- Database read accounts may also read the hub's own database, through a separate "Banco do hub" option (never
  as one more app; the audit line says so). Sessions and tokens there are hashes; the vault, users and audit live in
  files. `postgres` and the templates stay closed.

### Changed
- Database access texts name no particular tool ("SQL clients").
- The read account's credentials also show the databases it reads (one connection per database in the client).
- `hub-check` runs the hub's tests in parallel (pytest-xdist, `HUB_CHECK_WORKERS`, default 4: ~5 min instead of ~11)
  and does not test the same code twice: a green full run records the code's identity (docs and the release's
  version line do not count), so the push hook and the release's push skip it (`HUB_CHECK_FORCE=1` forces).

## [0.6.1] — 2026-11-25

### Changed
- Database read access: the allowed networks are optional (`--db-allowed-cidrs` or an empty answer): without them,
  read accounts connect from any origin that reaches the port, for servers reachable only from the corporate
  network or VPN. TLS, password and read-only stay; Docker gateways stay refused; a typed-but-invalid network
  still shuts the door.

## [0.6.0] — 2026-11-25

### Added
- Read access to the apps' database from other machines (dashboards, SQL clients) by host and port: off by default;
  `install.sh --db-public-port 5432 --db-allowed-cidrs …` (or the installer's question, also asked once on an existing
  install). Only read accounts get in from outside, over TLS, from the allowed networks; the service roles stay on the
  Docker networks; the hub writes `pg_hba.conf` and the certificate. Administração › Usuários › **Acesso ao banco**:
  accounts with a responsible person, chosen databases (never `hub`), validity, read only; new password, removal;
  suspended when the person is deactivated or loses every access.

## [0.5.1] — 2026-11-25

### Fixed
- CLI behind a corporate proxy that inspects TLS: `hub login`, `hub deploy` and `hub mcp` trust the system's
  certificate store (and `SSL_CERT_FILE`/`SSL_CERT_DIR`) besides the public CAs, and a refused certificate says why
  ("certificado TLS do hub não confiável (…)") and how to fix it instead of a bare `ConnectError`. Install docs
  mention `uv tool install --native-tls`.

## [0.5.0] — 2026-11-25

### Added
- `hub login` signs the CLI in through the browser (OAuth device flow, RFC 8628): the CLI prints a link and a code,
  the person approves them signed in to the hub, and the CLI then acts as that person (with their permissions) on
  every surface: `hub deploy`, the API v1, `hub mcp` and the apps' APIs. The session renews itself, ends after 30 days
  unused or 90 days in total, and is listed and revocable in My account › Access; `hub logout` ends it. An agent that
  gets "não autenticado" runs `hub login` and relays the link and the code (MCP instructions and the agents' guide
  say so).
- Users: "Revogar todos os acessos" ends a person's sessions, personal tokens and authorized apps (CLI, MCP) at once
  (`POST /api/admin/users/{id}/revoke-access`, MCP `users_revoke_access`).

### Removed (breaking)
- Access › Credentials (`/admin/tokens`) and `/api/admin/tokens` (API v1/MCP `tokens_list`, `tokens_revoke`,
  `tokens_delete`): every credential now belongs to a person. A deploy/CI token is a personal token with the
  `upload` and `status` actions (My account › Access), limited by its owner's permissions; `/admin/tokens` redirects
  there. On start-up each existing owner-less CI token goes to the person who created it (by name; revoked when
  that is not exactly one person), so pipelines keep working. The administration's list of AI clients went with the
  page: each person sees and revokes their own authorized apps.

### Fixed
- Entra ID: a first sign-in never gets an admin role from `HUB_ENTRA_DEFAULT_ROLE` (a role that manages users falls
  back to `User`; admin only through a mapped group or an administrator); the installer no longer offers it.
- Entra ID: the installer's account ("Administrador") takes the person's real name on the first sign-in with the
  same e-mail.

### Changed
- No banners on hub screens: the Status page's overall state, a degraded container, an app in maintenance
  (Backups), the update notice (Health) and sign-in errors are a muted line instead of a coloured strip.
- Users: one's own SSO account shows the ⋯ disabled, with the reason, instead of an empty menu.
- Containers (`/admin/runtime`): the host's CPU and memory bars slide to the new values instead of jumping.

## [0.4.2] — 2026-11-24

### Added
- App sidebar collapse on the desktop: the SDK sidebar has a "Recolher menu" button (`data-hub-menu="collapse"`);
  icons only, labels kept as hints and for screen readers; one choice per browser for every app. Phones keep the
  drawer.
- App tokens `--app-brand`/`--app-on-brand` color the logo tile of the app's sidebar (light and dark).
- SDK: `sdk.outbox.in_transaction(conn, ev)` writes audit events and Inbox notifications in the action's SQLAlchemy
  transaction (Postgres; in-memory under `hub dev`); the app's own SVG icons can be passed as plain strings
  (no `Markup`, no ruff S704).
- `hub --version` shows the CLI, the hub it was built from and the bundled SDK.
- Guide "Adaptar um app existente": a real port (Frota+) with what went where and the code that changed.

### Changed
- Template: a valid form POST answers `303` (Post/Redirect/Get) and writes its audit event in the same
  transaction; the agent guide, the template's `AGENTS.md` and the contract agree on POST responses, colors only
  in token declarations, notification recipients (`to_roles`, `hub dev --role`) and reserved template names.

### Fixed
- Contract §29 and §16/§17 pointed to sample apps and files that no longer exist.
- Staging pages never loaded the app ("Aplicação indisponível"): their CSP allowed only the production fragment URL;
  it now allows the `/staging/…` fragment, WebSocket and form URLs and the scripts approved for staging.
- A "⋯" row menu that fits neither below nor above inside its panel (e.g. a table with one row) was cut by the panel
  and its items could not be clicked; it now opens as a fixed layer next to the button (scrolling closes it).
- `hub` CLI without the platform package: building the parser imported `hub` for `--version` and every command
  failed with `ModuleNotFoundError`; `--version` now says "(not installed)" and `hub dev` explains what is missing.
- Variables: the type label ("Connection string") shows its full text as a hint when the column cuts it.
- Isolated validation instance (`iso-instance.sh`): creates each sample app's own database role, as the stack does;
  apps deployed by the hub no longer fail to sign in to their database.

## [0.4.1] — 2026-11-24

### Fixed
- MCP sign-in (OAuth) behind a corporate proxy: the client document (e.g. GitHub Copilot desktop's
  `client-metadata.json`) failed with "certificate verify failed" because the hub pinned the vetted IP in the URL and,
  through the proxy tunnel, TLS checked the certificate against that IP. Through the proxy the hub now connects by name
  (the public-IP vetting still runs first); direct connections keep the pinned IP.

### Changed
- `hub_update.py`: after the hub swap, applies the rest of the compose (apps, edge, databases) without recreating the
  hub; only services whose configuration changed are recreated. `--hub-only` keeps the old behavior. Builds (here and
  in `install.sh`) skip the buildx provenance attestation: it carries the build time, so every build got a new image
  id and compose recreated every app.
- The hub header no longer has a search box (users took it for the open app's own search); Ctrl+K still opens the
  search palette.
- Agent guide (`platform/docs/dev/AGENTS.md`, porting an app): where each item of the app's own top bar goes (global
  search → federated `/_hub/search`, bell → Inbox events, user menu → the hub header, logo → page header).
- No banners on hub screens: admin/tasks no longer shows "No response from: …" (an app that does not answer just
  lists no tasks; the reason stays in `/api/ops/tasks` → `errors`); "Runtime unavailable" and the Connections notices
  are a muted line under the title.
- admin/deploys: each deployment keeps its validation pipeline and its log ("[date time]: …" lines: steps, build output,
  rollout result; a later approval or rollback appends) and the History side panel shows it per version; a failed import or action
  shows a short notice pointing there instead of the long message at the bottom of the page. The Import package tab
  lost its manifest.json note.
- admin/runtime: CPU and memory always as x / y (a stopped container uses 0); where the engine cannot measure each
  container (rootless Podman without cgroup delegation) the hub reads the container's own /proc (sum of its
  processes). The host panel shows the bars with a 25% scale and, in a second panel, a stacked area chart of the last
  5 minutes (system, platform, apps; the top is the total) with the value of each instant on hover.
- Sample apps: buttons that only pretended to save now say they are a demo (or open the real form); the inventory list
  reloads with the current filters after a create; dashboard numbers come from the database where it exists;
  supplier contract status is computed from the due date; times follow the configured zone (`hub_sdk.locale.zone()`,
  `HUB_TZ`); missing English texts added.
- The platform default language (Settings) applies to users without a preference of their own, before the browser's
  language ("Automático", the default, keeps the browser deciding; saving other settings no longer pins pt-BR); numbers in translated texts follow the language; `/status` counts a new app's availability since its
  first check ("since dd/mm"); every registered app is monitored in Health; a fresh install no longer shows "restart
  pending" (the boot syncs mirror what compose already gave the containers) nor example variables bound to nothing;
  one display name per app on every screen.
- admin/runtime: the "Runtime unavailable" banner names the likely cause (socket missing, no permission, connection
  refused, timeout) instead of the raw engine exception.

## [0.4.0] — 2026-11-23

### Attention
- **Sample apps renamed to English ids and routes:** `ativos`→`inventory`, `fornecedores`→`suppliers`,
  `servicos`→`servicedesk`, `coletor`→`collector`, `automacoes`→`automations` (URLs `/apps/<id>/…`, containers,
  databases, roles, secrets, buckets, job kinds; in-app routes in English too, e.g. `catalogo`→`catalog`,
  `execucoes`→`runs`, `folha`→`sheet`, `escanear`→`scan`, `itens`→`items`). Screens keep following each user's
  language. An existing install migrates once with `scripts/rename-sample-apps.sh` (below); old bookmarks to
  `/apps/<old-id>` stop working. The audit chain keeps the old ids in past events (it is signed).
- App-specific variables follow the ids (e.g. `AUTOMACOES_ADMIN_ROLES`→`AUTOMATIONS_ADMIN_ROLES`,
  `HUB_SERVICOS_APPROVER_ROLES`→`HUB_SERVICEDESK_APPROVER_ROLES`); the migration renames them in the hub's variables
  and in `.prod.env`.

### Upgrade from 0.3.x
From `platform-py/` (the sample apps and the hub stop for a few minutes):

1. Backup (`docs/ATUALIZACAO.md` §1).
2. `git fetch --tags && git checkout v0.4.0`
3. `bash scripts/install.sh --config-only --yes` (new secret names are generated only if missing).
4. `dc build` — the new images (hub and sample apps).
5. `bash scripts/rename-sample-apps.sh --yes` — dumps the apps' Postgres and `.prod.env` to `.secrets/rename-apps/`,
   stops the hub and the old sample containers, renames databases/roles/job kinds, copies the buckets, renames the
   `.prod.env` keys and the hub data, removes the old containers. Safe to run again.
6. `dc up -d` — the hub and the sample apps under the new ids.
7. Check: `/healthz` answers `"version":"0.4.0"`, every `<project>-app-*` container is `(healthy)`, the catalog opens
   `/apps/inventory`, and staging of a sample app (if you used it) is deployed again from Implantações.

Rollback: `bash scripts/rename-sample-apps.sh --yes --reverse`, then `git checkout v0.3.2 && dc build && dc up -d`
(or restore the step 1 backup). Not using the sample apps (`--no-demo-apps`)? Run step 5 anyway: it renames only
what exists.

## [0.3.2] — 2026-11-23

### Upgrade from 0.3.1
Only the hub changes. From `platform-py/`: `git fetch --tags && git checkout v0.3.2`, then
`python scripts/hub_update.py --build --project <project>` (no downtime). Check that `/healthz` answers
`"version":"0.3.2"`. Coming from 0.2.0 or 0.3.0, follow those versions' steps first.

### Changed
- Containers page: the host panel says when it was measured ("Atualizado às HH:MM:SS" / "Updated at …", GMT-3 like
  the other screens) instead of "current usage · updates itself".
- Menu icons: Status shows the gauge; the Operação group (tasks, backups, health, logs) shows a wrench.

## [0.3.1] — 2026-11-23

### Upgrade from 0.3.0
Only the hub changes (no compose, secrets or sample app change). From `platform-py/`:

1. `git fetch --tags && git checkout v0.3.1`
2. `python scripts/hub_update.py --build --project <project>` — swaps the hub without downtime (backup, copy, watch,
   automatic rollback). Measured: one lost check of ~0.3 s; the apps are not touched.
3. Check: `curl -s http://127.0.0.1:<port>/healthz` answers `"version":"0.3.1"`.

Coming from 0.2.0, follow the 0.3.0 steps first. Rollback: `python scripts/hub_update.py --rollback`.

### Fixed
- **Rootless Podman:** `hub_update.py`, the install guard (`install.sh`, `hub-start.sh`, `dev.sh`) and `db-merge.sh`
  looked for the old `<project>-db` container with an untyped `docker inspect`, which also matches volumes (and, on
  Podman, name prefixes). With the leftover `<project>-db-data` volume, the zero-downtime update refused to run
  ("still on two servers") and a rerun of `install.sh` or a reboot (`hub-start.sh`) could stop the same way. They now
  inspect containers only. Measured on a RHEL-like server with rootless Podman: the hub swap had one lost check
  (~0.3 s) instead of the ~19 s stop of `dc up -d`.

### Changed
- Containers page (`/admin/runtime`): "Host capacity" shows the **measured usage** of the whole host (its `/proc`)
  split into system, platform and applications from each container's numbers, refreshed every 5 s; each segment's
  hover gives value / total (share); applications are magenta (green read as "free"); the runtime address moved to
  the hover of an "i" next to the title. When containers cannot be measured, the usage shows whole ("In use, no
  per-container detail"). Admission of new apps still uses the reserved limits.
- App status labels follow status pages: Operational, Degraded (was "Unstable") and Outage (pt-BR: Operacional,
  Degradado, Indisponível).

## [0.3.0] — 2026-11-23

### Attention
- **One Postgres only:** the hub's `hub` database now lives in the apps' server (`app-hub-app-db`); the compose no
  longer has `app-hub-db` and `APP_HUB_DB_PASSWORD` is no longer used. An existing install must move its hub database
  once with `scripts/db-merge.sh` (dump, restore into an empty target, row counts compared, old server kept stopped
  for rollback): see «Moving to the single Postgres» in `docs/ATUALIZACAO.md`. Until then `install.sh`, `hub-start.sh`,
  `dev.sh` and `hub_update.py` stop with that instruction instead of starting an empty hub.
- New secrets `APP_HUB_APP_DB_PASSWORD_<APP>` (the sample apps' own roles): `install.sh --config-only --yes` on an
  existing `.prod.env` generates only what is missing.
- Default roles are now three: `Administrador`, `Power User` (everything but managing users) and `User` (apps only;
  the former `Colaborador`). Existing installs keep their roles; SSO sign-ins fall back to `User`, then to the legacy
  `Colaborador`. `install.sh --entra-role` takes `User` (default), `"Power User"` or `Administrador`.
- New sample app `automacoes`: `install.sh --config-only --yes` adds its secrets (`APP_HUB_APP_DB_PASSWORD_AUTOMACOES`,
  `HUB_INTERNAL_TOKEN_AUTOMACOES`); the next `dc up -d` recreates the hub once (new network and broker range, ~10 s).
- Sessions: 24 h without use (was 30 min), renewed at most once per hour while browsing, 3 days after the login at
  most (was 12 h) — `HUB_SESSION_IDLE_MIN`, `HUB_SESSION_RENEW_MIN`, `HUB_SESSION_ABSOLUTE_HOURS`.

### Upgrade from 0.2.0
Run from `platform-py/` with `dc() { docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml "$@"; }`
(add `--profile demo-apps` only if `COMPOSE_PROFILES` is not set in `.prod.env`):

1. Backup (`docs/ATUALIZACAO.md` §1) and keep the vault master key and `.prod.env` off the server.
2. `git fetch --tags && git checkout v0.3.0`
3. `bash scripts/install.sh --config-only --yes` — adds only the missing secrets (sample apps' database roles,
   `automacoes`); nothing else in `.prod.env` changes.
4. Only if a `<project>-db` container still exists (installs from before the single Postgres): follow «Moving to the
   single Postgres» in `docs/ATUALIZACAO.md` (`dc build`, `dc up -d --no-deps app-hub-app-db`,
   `bash scripts/db-merge.sh --project <project>`, `dc up -d`) and skip step 5.
5. `python scripts/hub_update.py --build --project <project>` — swaps the hub without downtime (backup, copy, watch,
   automatic rollback).
   If it stops with "a borda <project>-edge não está no ar" (installs without the edge yet), skip it: step 6 creates
   the edge and takes over the port (~10 s, once). Podman hosts run it through `podman-docker` (as `install.sh` requires).
6. `dc build && dc up -d` — rebuilds the sample apps, creates `automacoes` and applies this version's compose changes.
   This step recreates the hub once (new app network and broker range) and each sample app.
7. Check: `curl -s http://127.0.0.1:<port>/healthz` answers `"version":"0.3.0"`; `docker ps` shows every
   `<project>-app-*` container `(healthy)`; sign in and open an app; Administração › Saúde has no alert.

Measured on a test install (0.2.0 → 0.3.0, sample apps on): step 5 took 2 min with **no** downtime; step 6 took
~40 s (build + up) with the **hub unavailable ~10 s** and **each sample app ~17 s** (recreated by compose, no
blue/green for them). Signed-in sessions survive. Apps you deploy through the hub (packages) are not touched.

Rollback: `python scripts/hub_update.py --rollback` (hub), then `git checkout v0.2.0 && dc build && dc up -d`
(apps). Data written by 0.3.0 stays readable by 0.2.0 (additive migrations only); the extra roles and the
`automacoes` database are simply unused there.

### Changed
- Sample apps ask for half a CPU each (manifests, registry and compose `cpus: 0.5`).
- Entra ID accounts have no "Reset password" (screen and API: 409) and no "Change password" in the account.
- `admin/runtime`: host capacity on the left, runtime on the right; the "limits above the host" note is gone.

### Fixed
- Signing in again after leaving sent the browser to `GET /logout` ("Method Not Allowed"): a POST that met an ended
  session no longer becomes `next`, and the login never returns to `/logout`.
- Content inserted by the app kit (a partial answer, the page a modal form lands on) now keeps `data-hub-poll` going.
- Checkboxes and radios inside `.form-field` were stretched to the whole row.
- Row hover in tables left a lighter strip under the actions column; the select arrow could render as two triangles.

### Added
- Sample app **Automações** (`automacoes`): a small automation orchestrator — versioned Python packages (upload on the
  screen, through the API with an `app-api` token, or MCP), processes pinned to a package version, scheduled triggers
  (interval or daily, a single fire across processes) and runs (durable jobs, shown in Tasks) with the form generated
  from the package fields and validated on the server. Package code runs in a subprocess without the app's secrets.
  App admins = hub roles in `AUTOMACOES_ADMIN_ROLES` (default `Administrador,Power User`); others run and see their runs.
- Conformance profile key `"roles"`: the hub roles of the suite's identity (for APIs restricted to a role).
- Adapting an existing app: a new section in the agents' guide (`/dev/docs/AGENTS.md`) with the inventory, URLs, layout, identity tokens and a JavaScript → `data-hub-*` table; the "Copy prompt" now also covers adapting. Measured with a sample FastAPI + Jinja app adapted by a fresh agent: 31/36 checks before, 36/36 after.
- App kit: `<select data-hub-autosubmit>` (filter on change); `data-hub-confirm-title`, `-label` and `-danger` (the app's own wording and red button); a partial form (e.g. in a modal) that ends in a redirect closes the modal and shows the destination page.
- SDK: common sidebar icons (truck, wrench, user, users, calendar, list, box, tag, money, map, home, search, bell), the app's own SVG as `Markup` in `NavItem.icon`, and `AppInfo(help_card=False)`.
- `hub check`: every `/apps/<id>/…` link of the screens must open (page and fragment), and own colors (`--app-*`) need the dark theme (§29).
- Page description behind an "i" next to the title: it opens a side panel (also for static notes, e.g. backups); the subtitle line under the title is gone. Status alerts stay on the page.
- Hub version in the header, small and right after the GitHub icon, for signed-in people (hidden on phones under 480 px and for anonymous visitors).
- Staging has its own URL: `/staging/apps/<id>/…` (production stays `/apps/<id>/…`). The path picks the
  environment for pages, fragments, the app API and WebSockets; `x-hub-base` and the app's redirects carry the
  prefix. The `hub_env` cookie (set at `/hub-env`) only sends `/apps/<id>` to the staging URL. A "STAGING" badge
  shows in the header while in staging.
- Apps hidden from the catalog (Settings, "Hidden from the catalog"): only users with access to the app still see
  it; the others no longer find it under "Other applications".
- Status page with a 12-month view (one bar per month). Daily availability is kept for 400 days in
  `uptime-daily.json` (the detailed metrics last 30 days).
- `install.sh` without questions for sign-in with Microsoft: `--entra-tenant`, `--entra-client-id`,
  `--entra-client-secret-file` (secret to the vault), `--entra-domains`, `--entra-role`, `--local-login`; the end of
  the install prints the Redirect URI to register. `--trusted-proxy <CIDR>` for a reverse proxy in front (e.g. an
  existing Nginx with TLS, documented in `docs/REDE-CORPORATIVA.md`).
- Per-app limits on the database role: `statement_timeout`, `idle_in_transaction_session_timeout` and
  `temp_file_limit` (`HUB_APP_DB_*`), applied when provisioning and refreshed on an already provisioned app. The
  Postgres keeps 30 connections for the hub (`superuser_reserved_connections`, `max_connections=200`).
- `install.sh` rerun: secrets added to `prod.env.example` in later versions are generated into the existing
  `.prod.env` (names shown, values never). The install notice says there is one Postgres.

### Fixed
- Deep links of nested app routes (`/apps/<id>/veiculos/12/editar`) opened a 404 on reload or bookmark.
- Post/Redirect/Get inside the hub: a redirect answered to a fragment request now stays a fragment (it embedded the whole hub page in the app screen), and the address bar follows the app's redirects.
- The app name in the sidebar ignored the app's sidebar colors (white on a light sidebar); chart axis labels were cut (compact numbers now); the phone app-menu button has an accessible name.
- `hub-check --all` refreshes the hub copy embedded in the CLI environment before testing it (it was a month old).
- Login: the SSO button reads "Microsoft SSO".
- English screens no longer leak Portuguese: /developers navigation, deploy/backup/runtime labels, the 403 page, the pager count, staging error pages and texts written by the shell JavaScript now have catalog pairs; a test renders every menu screen and every /developers page in English.
- Row "⋯" menus open upwards when a panel with overflow would cut them (e.g. the last row of /admin/users).
- Admin runtime: the host capacity bars no longer paint Platform and Applications in the same color; the engine caveats ("Connected to…", per-container usage, CPU limit) and the Docker/Podman chips, which only relabelled a read-only field, left the page (the caveat stays in `/api/ops/runtime`).
- Signing in with SSO never takes over a local account with admin permissions (e.g. the installer's admin with the
  same e-mail, even if linked before): the SSO login gets its own account with the default role and the person's
  name; admin only by assignment or by the Entra group mapping.
- Status headline said "All systems operational" with an unstable app: it now follows the apps' states.
- A new app deployed only to staging showed as `Unstable` in the catalog: the hub probed its production upstream,
  which does not exist yet. Production probes now start once the app has a production deployment.
- The stack ("i") on the Applications cards is shown only to admins (`editar_configuracoes`); the version stays on
  every card.
- Deploy on rootless Podman answering 502 after reporting success: the candidate is created as `<name>__new` and
  renamed, and Podman's DNS keeps the creation name. The candidate now gets its final name as a network alias; the
  first staging candidate no longer takes production's aliases (they split production traffic); and the deploy only
  routes to the final name once the hub reaches it by that name, else it fails with the cause (alias DNS).
- Containers page on rootless Podman without cgroup delegation: the engine reported the user's whole cgroup for every
  container (same ~2 GiB and ~200 processes everywhere, above each one's limit); usage now shows as unavailable with
  the reason, and a warning says when the CPU limit is not enforced. The page shows the engine socket on the host
  (`HUB_CONTAINER_SOCKET_HOST`) instead of the path mounted inside the hub, local images without `docker.io/library/`,
  and limits above the host as overcommit instead of a negative free share.
- Install behind a corporate proxy: the sample bucket is created again (the temporary `mc` container inherited the
  engine's proxy and sent the internal call to it); a failure now shows how to create it by hand.
- `install.sh --doctor` without systemd: the proof build uses cgroupfs like the hub's own socket (it failed with
  `did not get container create message from subprocess: EOF`, a false negative).
- `--ca-file` with a whole system bundle (`Argument list too long` in the builds) is refused with a clear message.
- The doctor names `--no-proxy <host>` when an internal mirror fails through the proxy.
- An install rerun warns about `--port`/`--bind`/`--public-url`/`--project`/`--subnet`/`--no-demo-apps` that differ from
  the existing `.prod.env` (they were ignored silently).
- Rootless Podman on a server whose administrator disabled the user manager (`user@.service` replaced, e.g.
  `ExecStart=/bin/true`): the install detects it and goes on without systemd (own socket, cron start) instead of
  asking for a `systemctl restart user@<uid>.service` that cannot help.
- Rootless Podman with `--no-systemd` on a systemd host whose user session has no bus (the administrator would not
  restart `user@<uid>.service`): the hub's own socket gets a `systemd-run` that starts aardvark-dns directly
  (`scripts/install/nosystemd/`), and the install's `docker`/`podman` commands go through that socket
  (`CONTAINER_HOST`), so builds no longer fail with `did not get container create message from subprocess: EOF`.
  The install's stop message offers `--no-systemd` when the administrator says no.
- `hub-start.sh` (cron `@reboot`) found no compose: cron's `PATH` lacks `~/.local/bin`, where `--get-compose` puts it.

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
- Home without the Inbox and Shortcuts widgets (the bell and the menu already reach both); saved layouts that
  had them still load.
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
