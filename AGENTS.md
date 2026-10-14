# AGENTS.md

Guide for coding agents (Claude Code, Codex and others) working in this repository.

## Branches and where to work

- **`main`**: official stack — the hub in **Python** (`platform-py/`) and the Python apps (`platform/demo-apps-py/`). Since 2026-10-07 it serves the owner's production instance. It also keeps the original static mockup (root: `index.html`, `js/`, `css/`) and the product documentation (`docs/PLAN.md`, `docs/FAQ-ARQUITETURA.md`).
- **Read first:** `docs/PENDENCIAS.md` (status and what is missing), `docs/CONTRATO-APP.md` (HTTP contract of the apps, in `platform/docs/`), `docs/HANDOFF.md`.

## Stack (fixed decisions)

Python 3.12 managed by `uv`; FastAPI + Jinja2 (SSR), asyncpg, httpx, Pydantic v2 + pydantic-settings, the `docker` SDK. Quality: **ruff** (lint and formatting, including the `S` rules from bandit), **Astral ty** (types), **pytest + pytest-cov** (lines + branches, 80% floor on the core), **`uv audit`**. The front-end (CSS, `hub*.js`, fonts, `catalog.json`) lives in `platform/assets/shell` (shell) and `platform/assets/web` (hub screens); app registry in `platform/config/` — do not rewrite. Official apps are Python only (SDK `hub_sdk` in `platform/sdk-python`).

## Commands (in `platform-py/`)

- Full check: `uv run hub-check` (ruff, ruff format, ty, `uv audit`, pytest with coverage; `--fast` = ruff/format/ty only; `--all` = + cli, SDK and each app). Database tests use a disposable Postgres.
- Stack: compose project `app-hub` (sealed image `app-hub-web`; `dev.sh up` mounts the code) — `app-hub-web` at `127.0.0.1:8090`, `app-hub-db`, `app-hub-objstore` (MinIO), apps `app-hub-app-<id>` (`docker-compose.apps.yml`), dev secrets in `.dev.env` (gitignored; never commit or print).
- Fresh install / another instance on the same host: `bash scripts/install.sh [--project app-hub-x --port 8095 --subnet 10.241] [--no-demo-apps]` (compose parameterized by `HUB_PROJECT`/`HUB_PORT`/`HUB_SUBNET`; sample apps in the `demo-apps` profile). Version: `hub/__init__.py` (`/healthz`), `CHANGELOG.md`, `scripts/release.sh <version>`; license Apache-2.0 (`NOTICE`, `TRADEMARKS.md`).
- `bash scripts/dev.sh up|down|stop|status|logs|reload-on|reload-off|obs-up|obs-down` — dev mode (uvicorn `--reload`, SSE reload `/_dev/reload`), observability profile (OTel Collector + Jaeger).
- SharePoint simulator: `docker-compose.graphsim.yml` (profile `sharepoint-sim`, `app-hub-graphsim`).
- App conformance: `bash scripts/conformance.sh`. CLI: `platform-py/cli` (`hub pack`, `hub mcp`, `hub dev`).
- E2E and UI sweep against :8090: `platform/tests-e2e` (`npx playwright test`, `node ui-sweep/run.mjs [--quick]`). Resilience: `platform/loadtest/py-dr.mjs`, `py-resilience.mjs` (they only touch `app-hub-*`).

## Guardrails

- **Hooks (fresh clone):** `cd platform-py && uv sync && uv run pre-commit install --allow-missing-config --hook-type pre-commit --hook-type pre-push`. Pre-commit: ruff, ruff format, ty, secret guard (`platform-py/scripts/secret_guard.py`: `.dev.env`, keys/certificates, `hubt_`, cloudflared credentials), files > 1 MB. Pre-push: full `uv run hub-check`. Never `--no-verify`.
- **A green `uv run hub-check [--fast|--all]` is mandatory before commit/push** (works in PowerShell and Git Bash; `--all` includes `platform-py/cli`, `platform/sdk-python` and each app in `platform/demo-apps-py/*`). `scripts/check.sh` is only a shortcut. If you changed an app: also that app's own check and `bash scripts/conformance.sh`.
- **TDD:** new behavior or a bug starts with a failing test (red), then the minimal code that makes it pass (green), then refactoring with green tests. A fixed bug = a test that reproduces the bug. Edge cases and deliberate decisions (e.g. what is intentionally ignored) also become tests.
- **Which test each change requires:** pure function/rule → **unit** (pytest). Route, database, vault, gateway or contract (OpenAPI, i18n) → **integration** (pytest with `TestClient`/disposable Postgres). User-visible feature → **scenario** (full flow in one integration test) and, if it has a screen, **E2E** Playwright in `platform/tests-e2e` + ui-sweep. Changed an app or the app contract → conformance. Changed the gateway, session, database or anything on the path of every request → **load** (`platform/loadtest/py-load.mjs`, on an isolated stack, in the heavy validation) without worsening the p95 or error rate of the last recorded measurement in `docs/IMPLANTACAO.md`.
- **Pydantic v2 at the edges:** forms, API inputs and Administration writes are validated by Pydantic models, not by raw `dict`.
- **Secrets never** in HTML, API, logs, audit trail or git (`.dev.env` stays out of git); this includes connection credentials.
- **Every UI change passes the ui-sweep.**
- **Validation:** light during the day (`--fast`, tests for the affected code, ui-sweep `--quick`); heavy once at the end of the day (full E2E, DR, chaos).

## Code size and clean code

Short and imperative on purpose; based on Akita's "Clean Code for AI Agents".

- **Functions:** 4 to 20 lines, one thing each; split if longer. At most 2 levels of indentation: early returns instead of nested `if`.
- **Files:** under 500 lines (200–300 is ideal), one responsibility per module; no "god files".
- **Enforced by `hub-check`:** ruff `C901` (complexity > 10), `PLR0912` (> 12 branches), `PLR0915` (> 30 statements) in every project, and the file-size ratchet (`platform-py/size-baseline.json`). Existing excess is marked `# noqa: C901…` or listed in the baseline: touching such code means shrinking it, never growing it; remove the `noqa`/baseline entry once it fits. Never add a new `noqa` or baseline entry for these rules.
- **Names:** specific enough to grep (no `data`, `handler`, `Manager`); explicit types (no bare `dict`/`Any` at boundaries).
- **No duplication:** extract shared logic; reuse what already exists before writing new code.
- **Errors:** exception messages carry the offending value and the expected shape.
- **Comments:** WHY, not WHAT; keep existing ones.
- **Tests:** mock external I/O (API, DB, filesystem) with named fakes, not inline stubs; tests are fast, independent and repeatable.

## Architecture of the Python hub (`platform-py/hub`)

`auth` (sessions, RBAC, tokens), `vault` (vault), `state` (Postgres: sessions, jobs, leadership), `admin` (stores + chained audit trail), `gateway` (proxy `/apps/{id}`, `/_frag/{id}`, signed identity `x-hub-user`, `x-hub-base`, CSP, WebSocket), `ux` (Home, search, Inbox, settings, i18n), `connections`/`storage` (SharePoint/S3 and broker), `insight` (logs, flags, SLOs, observability, OTLP), `ops` (deployments, image build, blue/green, staging, supervisor, backups), `api` (single table that generates `/api/v1`, OpenAPI and MCP tools; MCP OAuth 2.1). Templates in `hub/web/templates`. **Route modules (pattern):** a module-level `router = APIRouter()` whose handlers take the shared state as a dependency (`ops: OpsCtx = Depends(ctx)`, see `hub/ops/opsctx.py` and `hub/ops/routes_*.py`); long work goes to service modules (`*_jobs.py`) and page rows to pure view functions (`*_view.py`). Never define routes as closures inside a `mount_*` function (ruff counts every nested route into the outer function). Menu (21 screens): `hub/web/nav.json` (`also` = other routes that light up the item) and tabs in `hub/ux/shell.py` (`TAB_SETS`: Users/Roles `?tab=roles`, Logs/Audit, Variables/Connections; My account = `/account`, `/account/security`, `/account/access`); Observability is a section of Health, usage (MAU) and key rotations ("Security and keys") live in Settings; old routes answer 308.

## Rules

- The hub only manages `app-hub-*` containers (guard in `hub/ops/docker_guard.py`). Never touch containers of other projects or kill processes by image name.
- Public tunnel = container `apphub-devtunnel` (config `platform/.tunnel/config-standalone.yml`): **never stop/remove without the owner's request**.
- App URLs: `/apps/{id}/…`; the app builds links with the public base (`x-hub-base`, `{{ base }}`), never a bare `/{id}/…`. Apps have no JavaScript of their own (behaviors via `data-hub-*` in `hub-kit.js`); readiness `GET /{app}/_hub/ready` is mandatory.
- New user-facing text → key in the catalog in both languages (pt-BR, en).
- One heavy run at a time (low-memory machine).

## Cleanup when finished (mandatory)

Every task removes what it created before being declared done. Only delete what **you** created in this task; when in doubt, list it and ask the owner.

- **Temporary files:** scripts, logs and outputs stay in the agent's temporary directory, never in the repository. Delete them at the end. No stray `*.log`, `*.hubapp`, `.coverage`, `j8080`/`*.html` inspection files in the repo; `git status` clean (apart from what the task delivers).
- **Test containers and volumes:** remove them by the exact name you gave (`docker rm -f <name>`, `docker volume rm <name>`), including simulated servers (e.g. `rhel-*`) and isolated stacks (`-p <test-project> down -v`). Never bulk `docker rm`/`volume prune`: there are volumes from other projects.
- **Images:** remove the ones you built that are no longer used (test tags, `:prev`, images of isolated stacks), then `docker image prune -f` (dangling only) and `docker builder prune -f` (build cache). Keep base images, the current `app-hub` images and the rollback pins `app-hub-app-*:keep-*`/`baseline-*`. Images from other projects are not yours, even when stopped.
- **Local build:** `__pycache__`, `.pytest_cache`, `.ruff_cache`, `build/`, `dist/` generated by the task and `.hub/` of test apps. Do not delete `.venv` or `uv.lock`.
- **Owner's instance:** if the task stopped it, start it again (`up -d`) and check `/readyz` before finishing. `apphub-devtunnel` never enters the cleanup.
- **Check:** `docker ps -a`, `docker system df` and `git status` at the end; report what you removed and what you deliberately left.

## Static mockup (root)

Pure front-end without a build step (`index.html`, `js/app.js` with routing by `pathname` and `data-route`, `css/styles.css`); static deploy via `vercel.json`/`_redirects`. Serve with SPA fallback (`npx serve -s .`). Central product rule (`docs/PLAN.md` §11): a single global platform header; each app's sidebar belongs to the workspace, never to the global rail.
