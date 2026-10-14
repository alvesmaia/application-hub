# E2E tests (wave 6) — Playwright

Presentation walkthrough, multiple tabs, and `hub dev` × production parity, against the compose stack with the
demo users (`HUB_DEMO_USERS=1`).

## Against the Python hub (production has no demo accounts)

`bash platform-py/scripts/tmp-instance.sh up` creates a temporary instance (a copy of the state, demo enabled only
there, no Docker socket, port 8093) and `platform-py/.tmp-instance.env` (demo password). Run with
`HUB=http://127.0.0.1:8093 node ui-sweep/run.mjs --quick` (load the `.env` first) and `tmp-instance.sh down` at the end.

## Run

```bash
bash platform-py/scripts/tmp-instance.sh up   # instance with demo users (above)
cd platform/tests-e2e
npm ci && npx playwright install chromium
npm run e2e                                # everything (demo projects + multitab), headless
npm run e2e:demo                           # presentation walkthrough only
npm run e2e:multitab                       # multiple-tab test only
npm run e2e:report                         # opens the HTML report (reports/html)
```

Variables: `HUB` (default `http://localhost:8080`), `HUB_STAGING` (default
`http://staging.localhost:8080`), `HUB_BIN` (`hub` CLI from `platform-py/cli`; default `hub` on the PATH),
`E2E_IMPORT_ROWS` (import spreadsheet, default 200 000 rows), `E2E_IMAGE_<APP>`/`E2E_IMAGE` (package image,
`<image>@<local Image ID>`, required in the scenarios that package).

Outputs: `reports/html` (report), `reports/results.json`, `reports/junit.xml`; on failure,
`test-results/<test>/` with screenshot, trace (`npx playwright show-trace …/trace.zip`) and video.
Single worker: the scenarios change shared state (deployments, backups, variables).
The sessions of the three users are created once (`tests/global-setup.ts` → `.auth/`).

## Scenarios (`tests/`)

| File | Scenario |
|---|---|
| `01-auth.spec.ts` | login through the form as admin, operator and reader; `next=`; Administration menu; 12 admin areas allowed/friendly 403 per role; API with the same rules |
| `02-apps.spec.ts` | each app from the catalog: loader on first open (`data-hub-loader=full`), each section with skeleton, active sidebar, single screen (sidebar footer and header visible without scrolling), browser back; deep link/reload/app already open without loader |
| `03-ativos.spec.ts` | search, filter and sorting on the server; same asset edited in two contexts (admin and operator) → 409 with the conflict message; import of 200 thousand rows with progress → completed + error-report download; CSV export (filtered) and XLSX (task → download) |
| `04-fornecedores-servicos.spec.ts` | attachment uploaded and downloaded byte for byte (sha256), PDF record sheet, external query (hub-extapi); heavy report of servicos + CSV; opening a ticket |
| `05-admin.spec.ts` | variables: create public and secret with bindings, reveal secret, rotate internal token, "Aplicar (reiniciar)" (Apply (restart)); roles (matrix); real-time logs; health "Verificar agora" (Check now); users create + reset password (change required on first access); API token; audit shows the actions |
| `06-ops.spec.ts` | package generated with `hub pack` (single version) → the 7 steps → staging → "Promover" (Promote) → "Aprovar" (Approve) → production → "Rollback"; Containers with the real engine state (stop/start checked with `docker inspect`); "Backup agora" (Back up now) + "Restaurar" (Restore) with "Em manutenção" (Under maintenance) during the restore; Tasks: cancel and reprocess an export in progress |
| `07-staging.spec.ts` | "Ver em staging" (View in staging) → confirmation → staging host (STAGING band, control plane 404) → leaving on the main host ends staging (401) |
| `multitab.spec.ts` | 5 tabs in one context: different apps and sections, reload, deep links without loader, log console in 3 tabs with **one** SSE connection (counted on the server by `GET /api/admin/logs/stats` and in the network events), failover when the leader closes, 409 across tabs, no connection left when everything is closed |
