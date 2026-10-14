# AGENTS.md — __APP_NAME__ (`__APP_ID__`)

Application Hub app. Guide for code agents (Claude Code, Codex…) and people.

## Stack

Python 3.12 (`uv`), FastAPI, Jinja2 (SSR), `hub_sdk` (hub SDK, in `vendor/`), SQLAlchemy Core + Alembic
(`hub_sdk.db`: SQLite in `.hub/dev.db` in dev, Postgres on the hub — same code), Pydantic v2 at the edges.
Quality: ruff (includes the `S` rules), ty, pytest + pytest-cov (80% floor).

## Commands

| Command | What it does |
|---|---|
| `hub dev` | Lightweight local hub (shell, gateway, simulated identity) + app with reload at `http://localhost:4173/apps/__APP_ID__` — no Docker, no Postgres |
| `hub check` | ruff, formatting, ty, pytest + coverage, manifest, local conformance and `openapi.json` |
| `hub deploy --env staging` | Packs and sends to the hub (production: `--env production`, stays pending approval) |
| `hub pack` | Builds the `.hubapp` for manual upload on the Deployments screen |

First time: `uv sync`. Run `hub check` before every commit.

## Conventions

- Routes under `/__APP_ID__/…`; the browser sees `/apps/__APP_ID__/…`. **Every link/`action`** uses `{{ base }}` (comes from the `x-hub-base` header) — never a bare `/__APP_ID__/…`.
- Screens = HTML fragments with the workspace (`templates/*.html` extend the SDK's `workspace.html`) and the hub classes (`app-page-header`, `content-panel`, `data-table`, `form-field`, `primary-button`, `metric-card`…). Colors only through hub CSS tokens (`var(--…)`) in `assets/app.css`.
- Screen behavior only through `data-hub-*` attributes (contract §25: modal, confirmation, filter, toast, poll…).
- `method="post"` forms: the hub submits via XHR and swaps the workspace with the response (return the page).
- Inputs (forms and API) are validated by Pydantic models. JSON API under `/__APP_ID__/api/v1/*`, each route with an `operation_id` (becomes an MCP tool); RFC 9457 errors (`hub_sdk.api.problem`); `require_user` requires an identity.
- Database schema only through Alembic migrations in `src/__PKG__/migrations/versions/` (applied on startup).
- What the app needs from the hub goes in `manifest.json` → `requires` (`database`, `storage`, `variables`, `secrets`).
- Files: `hub_sdk.storage.connection("<name>")` (under `hub dev` it writes to `.hub/storage/`).
- Readiness: `GET /__APP_ID__/_hub/ready` (already installed by `HubSdk`).

## Dashboards (light BI)

- `hub_sdk.dashboards` (extra `hub-sdk[dashboards]`; ready-made template: `hub project create <id> --template dashboard`):
  `Dataset` (SQL with `:name` parameters against an SQL connection), `Param` (query-string filters, validated), `Dashboard.load` (cache per filter combination, `ttl`), `kpi`/`kpis`, `line_chart`/`area_chart`/`bar_chart`/`pie_chart`/`donut_chart` (SVG rendered on the server, colors via tokens — light/dark), `table` (hub filter and pagination), `filters_form` (`<form method="get">`), `export_links` + `export_response` (CSV/XLSX).
- Data source: `hub_sdk.sql.connection("<name>")` — on the hub, an SQL connection from Administração › Conexões (PostgreSQL, MySQL/MariaDB, SQL Server) bound to app × environment, through the broker (read-only, row and time limits, audit without data; the app never sees the password); under `hub dev`, `.hub/connections.toml` (a copy of `dev/connections.toml`; example SQLite from `dev/<name>.sql`). The app's own database: `hub_sdk.sql.from_engine`.
- Portable, parameterized SQL (no f-strings with user values). The dashboard CSS (`dash.CSS`) ships with the `app.css` the app serves. Export via a `download` link to `/_frag/__APP_ID__/…`.
- Guide: `DASHBOARDS.md` (on the hub: Desenvolvedores).

## Forbidden

- Custom JavaScript, inline `<script>`, `on*=`, or `style=` with literal colors.
- Secrets in code, in `.env.example`, in HTML, logs, or API responses.
- Links without the public base, `<html>/<head>/<body>` in fragments, own `hub_*` cookies.
- Changing the schema without a migration; SQL built with f-strings (use SQLAlchemy Core).
- Running as root or writing outside `/scratch` in the image.

Full contract: `CONTRATO-APP.md` (on the hub: Desenvolvedores → Contrato do app).
