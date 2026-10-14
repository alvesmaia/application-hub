# Clean code — functions ≤ 20 lines, files < 500

## Foundation
- ✅ 1 · Foundation: AGENTS.md rules, ruff C901/PLR0912/PLR0915, file-size ratchet

## Hub (platform-py)
- ✅ 2 · Split hub/ops/routes.py into section modules
  - ✅ opsctx.py + routes_{deploys,runtime,backups,pages}.py
  - ✅ full pytest (483 passed)
  - ✅ E2E 06-ops/29 (7/7)
  - ✅ dropped from size-baseline
- ✅ 3 · Route handlers out of the mount closures (module-level router + OpsCtx)
  - ✅ routes_{runtime,deploys,backups,pages}: module routers + Depends(ctx)
  - ✅ ops tests (65 passed)
  - ✅ full pytest (483 passed)
  - ✅ E2E found a stale-cache race in rt.Engine (refresh in flight across a stop); fixed + test
  - ✅ capacity code moved to hub/ops/capacity.py (runtime.py 660 → 572)
  - ✅ re-run: pytest 484 passed, E2E 06-ops/29 7/7
  - ✅ long functions split: deploy_jobs.py, backup_jobs.py, backup_policy.py, runtime_view.py, backups_view.py (HTML identical)
  - ✅ OpsCtx is a class with the helpers; mount_ops only wires (79 lines); pattern in AGENTS.md
  - ✅ pytest 494 passed, E2E 06-ops/29 7/7
- ▶️ 4 · Split the other large hub files (oauth.py, app.py, 15 files 500–720) and clear the hub noqa (69)
  - ✅ oauth.py 1168 → oauth_{util,base,clients,authorize,tokens}.py + oauth.py (all < 300); pytest 494, E2E 13 2/2
  - ▶️ agent A: app.py, auth, state, connections, insight, vault, storage, graphsim
  - ▶️ agent B: api (mcp, v1, rest), admin, ops (remaining), ux, gateway

## SDK, apps, CLI, JS
- ✅ 5 · SDK Python: jobs.py, storage.py, idempotency.py, dashboards.py + 17 noqa
  - ✅ agent C: 4 files < 500, 17/17 noqa removed, public API unchanged (AST check); SDK 117 passed
  - ✅ dev docs regenerated (6 new SDK sections titled)
  - ⬜ DB-backed SDK tests (21 skipped without Postgres) in the heavy validation
- ▶️ 6 · Sample apps: app.py of ativos/coletor/servicos/fornecedores, ativos/importer.py + 21 noqa
  - ▶️ agent D (worktree)
- ✅ 7 · CLI and scripts: 9 noqa
  - ✅ agent E: 9/9 noqa removed (CLI 36 passed, scripts 7 passed)
- ▶️ 8 · Shell JS: hub-device.js, hub-ux.js, hub-kit.js, hub.js, ops.js
  - ✅ agent F: 5 files split into 15 (all < 500), baseline −5; pytest assets/CSP 173 passed
  - ⬜ browser E2E + ui-sweep after all merges (risks: load order, SW precache, Coletor camera/offline)

## Closing
- ⬜ 9 · Tighten limits (20 statements, complexity 8), empty baseline, heavy validation, CHANGELOG
