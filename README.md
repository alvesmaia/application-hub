# Application Hub

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> Português: [README.pt-BR.md](README.pt-BR.md) · Security: [SECURITY.md](SECURITY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
>
> **Status: experimental.** Not production-hardened unless you follow [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md). Docs are mostly in Portuguese.

A white-label corporate portal that hosts many internal applications under one global shell
(header + menu that never disappears). Each application runs isolated in its own container and is
composed into the page without iframes, plus Administration, Operations, observability, Inbox and
global search. Everything is also exposed as an **API** (`/api/v1`, OpenAPI 3.1, docs at `/api/docs`)
and over **MCP** for AI assistants (`/mcp`, same access control as the UI).

## Features

| Area | What you get |
|---|---|
| **Shell & UX** | Fixed global header and menu; Home, app catalog, Inbox (approvals and access requests), federated search (hub + apps), guided tour, light/dark theme, PT-BR/EN/ES, PWA |
| **Applications** | Each app in its own container, reached only through the gateway and composed into the page without iframes; same origin with strict CSP and sanitized HTML; real time via SSE and WebSocket; barcode scanning and camera (`hub-device`) |
| **Access & security** | Local login or Microsoft Entra ID SSO (with single logout); per-app roles and permissions; scoped personal API tokens; encrypted vault for secrets; network-restricted control plane |
| **Administration** | Users, roles, per-environment variables, connections (Postgres, MySQL, SQL Server, SharePoint/OneDrive, S3) behind a broker — apps never see credentials |
| **Operations** | Blue/green deployments with mandatory readiness and rollback, containers, scheduled tasks, least-privilege backups, zero-downtime hub updates |
| **Observability** | Metrics, SLOs and status page, usage and analytics, logs, OTLP export, verifiable hash-chained audit log (file or webhook anchor) |
| **API & AI** | Public API `/api/v1` (OpenAPI 3.1, docs at `/api/docs`); MCP server `/mcp` with OAuth 2.1 — hub and app tools under the same access control as the UI |
| **For developers** | Python SDK `hub_sdk`, `hub` CLI (`hub project create`, `hub dev`, `hub check --full` with the conformance suite, `hub deploy`), dev mode with reload, 4 sample apps (Assets, Suppliers, Services, Collector) |
| **Deployment** | Docker only: `install.sh` generates secrets, builds everything from source and creates the first admin; multiple instances per host; tested on rootless RHEL |

## Roadmap

| Item | Status | Notes |
|---|---|---|
| Built-in scale mode in compose (N hub replicas behind the edge, `round_robin`) | Planned | Today: 1 container ≈ 200 concurrent users; 2 containers ≈ 300 (manual setup) |
| Multiple processes per container (`uvicorn --workers`) | Planned | Each process tries to bind the broker's internal port (8081) |
| ODBC in the SQL broker | Idea | Only with a specific database driver (Oracle, DB2…) in the image; Postgres, MySQL and SQL Server are native |
| Human approval of agent actions in the Inbox | Idea | Today destructive actions are confirmed by the AI client (`confirm_token`) |
| English i18n keys and English ids for the sample apps | Idea | Code, comments and docs are already English; these touch every UI string and require data migration |

Details and history: [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md).

## Dependencies

Pinned versions as of this release (Python packages: `uv.lock`; images: compose files and Dockerfiles).
Licenses of bundled third-party files are in [`NOTICE`](NOTICE).

**Services (containers)**

| Component | Version / image | Role | License | Defined in |
|---|---|---|---|---|
| Caddy | `caddy:2.10.0-alpine` (Docker Hub) | Edge reverse proxy (port 8090; zero-downtime swaps) | Apache-2.0 | `platform-py/docker-compose.yml`, `platform-py/infra/edge/Caddyfile` |
| PostgreSQL | `postgres:16-alpine` (Docker Hub) | Hub state database and apps' database | PostgreSQL License | `platform-py/docker-compose.yml`, `docker-compose.apps.yml` |
| MinIO + mc | `RELEASE.2025-09-07T16-13-09Z` / `RELEASE.2025-08-13T08-35-41Z`, built from source | S3-compatible object storage (app files, backups) | AGPL-3.0 (separate program, network only) | `platform-py/infra/minio/Dockerfile` |
| Python | `python:3.12-slim` (hub), `python:3.12-slim-bookworm` (apps) | Runtime of the hub and the sample apps | PSF-2.0 | `platform-py/Dockerfile`, app Dockerfiles |
| OpenTelemetry Collector | `otel/opentelemetry-collector-contrib:0.135.0` | Optional: telemetry (`observability` profile) | Apache-2.0 | `platform-py/docker-compose.observability.yml` |
| Jaeger | `jaegertracing/jaeger:2.10.0` | Optional: trace viewer (`observability` profile) | Apache-2.0 | `platform-py/docker-compose.observability.yml` |

**Python packages (hub)**

| Package | Version | Use | License |
|---|---|---|---|
| FastAPI | 0.142.2 | Web framework | MIT |
| Uvicorn | 0.54.0 | ASGI server | BSD-3-Clause |
| Jinja2 | 3.1.6 | Server-side templates | BSD-3-Clause |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 | Models and configuration | MIT |
| asyncpg | 0.31.0 | PostgreSQL driver | Apache-2.0 |
| PyMySQL | 1.2.3 | MySQL/MariaDB connections (SQL broker) | MIT |
| python-tds | 1.17.1 | SQL Server connections (SQL broker) | MIT |
| cryptography | 50.0.2 | Vault encryption, signatures | Apache-2.0 / BSD-3-Clause |
| argon2-cffi | 25.1.0 | Password hashing | MIT |
| httpx | 0.28.1 | HTTP client (gateway, Entra ID, Graph) | BSD-3-Clause |
| websockets | 17.2 | WebSocket gateway | BSD-3-Clause |
| docker (docker-py) | 7.2.0 | Container runtime control | Apache-2.0 |
| markdown-it-py / Pygments | 4.2.0 / 2.21.0 | Developer portal rendering | MIT / BSD-2-Clause |

**Python packages (SDK and sample apps, optional extras)**

| Package | Version | Use | License |
|---|---|---|---|
| SQLAlchemy / Alembic | 2.0.44 / 1.17.1 | App database access and migrations | MIT |
| psycopg | 3.2.12 | PostgreSQL driver for apps | LGPL-3.0 |
| openpyxl / python-calamine | 3.1.5 / 0.8.2 | Excel write / read | MIT |

**Front-end (bundled in the repository)**

| Component | Version | Use | License |
|---|---|---|---|
| @zxing/library | 0.23.0 | Barcode/QR decoding in the browser | Apache-2.0 |
| Nunito Sans, Red Hat Display, Red Hat Mono | — | Fonts | SIL OFL 1.1 |
| Lucide icons | — | Theme icons | ISC |

## Architecture

- **Stack:** Python 3.12 (`uv`), FastAPI + Jinja2 (SSR), asyncpg, PostgreSQL, MinIO, Docker.
- Hub in `platform-py/hub` (auth, vault, state, admin, gateway, ux, connections/storage, insight, ops, api).
- Python apps, the `hub_sdk` SDK, shared front-end assets (`platform/assets`) and the conformance suite live in `platform/`.
- Apps are separate upstreams reached only through the gateway (`/apps/<id>/…`, `/_frag/<id>/…`); the shell composes
  their HTML fragments (sanitized, same origin, no app JavaScript unless approved). Contract: `platform/docs/CONTRATO-APP.md`.
- Branches: `main` = official Python stack; `stack/python` = working branch.

## Install from scratch (Docker only)

```bash
git clone https://github.com/alvesmaia/application-hub.git && cd application-hub
bash platform-py/scripts/install.sh --admin-email you@company.com   # http://localhost:8090
```

Generates secrets (`platform-py/.prod.env`), builds everything from source (hub, apps, MinIO/mc), creates the vault
master key and the first administrator (temporary password in `platform-py/.secrets/primeiro-acesso.txt`). The 4
sample apps are on by default (compose profile `demo-apps`); `--no-demo-apps` installs the hub only. Upgrades:
`docs/ATUALIZACAO.md`; changes: `CHANGELOG.md`.

The interactive install asks the basics and then "Review advanced options?" — every option shows the **recommended**
value in [brackets] and Enter keeps it: MinIO data folder (e.g. a dedicated disk), audit anchor folder and webhook,
off-site backup copy (none / SharePoint / S3), sign-in with Microsoft Entra ID, admin network restriction,
proxy/tunnel in front, telemetry, new-version notice, public developer docs and build CPUs. Secrets are read without
echo (Entra and SharePoint secrets go to the vault). `--config-only` writes only `platform-py/.prod.env` to review
first. Without a terminal, SharePoint via `SP_TENANT`, `SP_CLIENT_ID`, `SP_SITE`, `SP_FOLDER`, `SP_SECRET_FILE`.

## Development (in `platform-py/`)

```bash
uv sync
uv run hub-check --all     # ruff, format, ty, audit, pytest (+ cli, SDK, apps)
docker compose --profile demo-apps -f docker-compose.yml -f docker-compose.apps.yml up -d --build   # hub at http://127.0.0.1:8090
bash scripts/dev.sh up     # dev mode (code from disk, --reload)
```

> ⚠️ Demo accounts (`HUB_DEMO_USERS=1`) are for development only. Production uses `docker-compose.prod.yml`
> + `prod.env.example` and the checklist in `docs/IMPLANTACAO.md`.

## Documentation map

| Document | Purpose |
|---|---|
| `AGENTS.md` | Working guide: stack, commands, guardrails |
| `docs/HANDOFF.md` | Current state and how to run |
| `docs/PENDENCIAS.md` | Status and open items |
| `docs/FAQ-ARQUITETURA.md` | Architecture decisions (Q&A) |
| `docs/IMPLANTACAO.md` | Deployment checklist |
| `platform/docs/CONTRATO-APP.md` | Hub ↔ app contract |

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and third-party notices in [`NOTICE`](NOTICE). The "Application Hub"
name and the Alvesmaia marks and logo are not licensed for forks' branding: see [`TRADEMARKS.md`](TRADEMARKS.md).
