# Application Hub

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> Português: [README.md](README.md) · Security: [SECURITY.md](SECURITY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
>
> **Status: experimental.** Not production-hardened unless you follow [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md). Docs are mostly in Portuguese.

A white-label corporate portal that hosts many internal applications under one global shell
(header + menu that never disappears). Each application runs isolated in its own container and is
composed into the page without iframes, plus Administration, Operations, observability, Inbox and
global search. Everything is also exposed as an **API** (`/api/v1`, OpenAPI 3.1, docs at `/api/docs`)
and over **MCP** for AI assistants (`/mcp`, same access control as the UI).

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
| `docs/migracao/` | Python migration history (older docs in `historico-rust/`) |

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and third-party notices in [`NOTICE`](NOTICE). The "Application Hub"
name and the Alvesmaia marks and logo are not licensed for forks' branding: see [`TRADEMARKS.md`](TRADEMARKS.md).
