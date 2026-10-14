# Clean code — functions ≤ 20 lines, files < 500

## Foundation
- ✅ 1 · Foundation: AGENTS.md rules, ruff C901/PLR0912/PLR0915, file-size ratchet

## Hub (platform-py)
- ✅ 2 · Split hub/ops/routes.py into section modules
  - ✅ opsctx.py + routes_{deploys,runtime,backups,pages}.py
  - ✅ full pytest (483 passed)
  - ✅ E2E 06-ops/29 (7/7)
  - ✅ dropped from size-baseline
- ▶️ 3 · Route handlers out of the mount closures (module-level router + OpsCtx)
  - ✅ routes_{runtime,deploys,backups,pages}: module routers + Depends(ctx)
  - ✅ ops tests (65 passed)
  - ✅ full pytest (483 passed)
  - ✅ E2E found a stale-cache race in rt.Engine (refresh in flight across a stop); fixed + test
  - ✅ capacity code moved to hub/ops/capacity.py (runtime.py 660 → 572)
  - ✅ re-run: pytest 484 passed, E2E 06-ops/29 7/7
  - ⬜ split long functions (run_import, start_backup, backup_action, runtime_page, backups_page)
  - ⬜ mount_ops helpers out of the closure; document the pattern in AGENTS.md
- ⬜ 4 · Split the other large hub files (oauth.py, app.py, 15 files 500–720) and clear the hub noqa (69)

## SDK, apps, CLI, JS
- ⬜ 5 · SDK Python: jobs.py, storage.py, idempotency.py, dashboards.py + 17 noqa
- ⬜ 6 · Sample apps: app.py of ativos/coletor/servicos/fornecedores, ativos/importer.py + 21 noqa
- ⬜ 7 · CLI and scripts: 9 noqa
- ⬜ 8 · Shell JS: hub-device.js, hub-ux.js, hub-kit.js, hub.js, ops.js

## Closing
- ⬜ 9 · Tighten limits (20 statements, complexity 8), empty baseline, heavy validation, CHANGELOG
