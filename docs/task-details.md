# Task details

✅ **Clean code (tasks 1–9)**

Finished 2026-10-15 (`c4fb303`). Final list as it stood in TASKS.md:

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
- ✅ 4 · Split the other large hub files (oauth.py, app.py, 15 files 500–720) and clear the hub noqa (69)
  - ✅ oauth.py 1168 → oauth_{util,base,clients,authorize,tokens}.py + oauth.py (all < 300); pytest 494, E2E 13 2/2
  - ✅ agent A: app.py 1119 → 142, auth/state/vault/storage/graphsim/insight/connections split; 8 out of the baseline
  - ✅ agent B: api/admin/ops/ux/gateway — all noqa in scope removed, 7 files out of the baseline; pytest 494
  - ✅ hub/filelock.py split
  - ✅ oauth*.py: last 7 functions split, mount_oauth → module router; responses identical
  - ✅ zero size noqa left in the repo; hub-check --all 30/30

## SDK, apps, CLI, JS
- ✅ 5 · SDK Python: jobs.py, storage.py, idempotency.py, dashboards.py + 17 noqa
  - ✅ agent C: 4 files < 500, 17/17 noqa removed, public API unchanged (AST check); SDK 117 passed
  - ✅ dev docs regenerated (6 new SDK sections titled)
  - ✅ DB-backed SDK tests with Postgres: 137 passed
- ✅ 6 · Sample apps: app.py of ativos/coletor/servicos/fornecedores, ativos/importer.py + 21 noqa
  - ✅ agent D: app.py 58–136 lines, routes/ per area, 21/21 noqa removed, OpenAPI identical
- ✅ 7 · CLI and scripts: 9 noqa
  - ✅ agent E: 9/9 noqa removed (CLI 36 passed, scripts 7 passed)
- ✅ 8 · Shell JS: hub-device.js, hub-ux.js, hub-kit.js, hub.js, ops.js
  - ✅ agent F: 5 files split into 15 (all < 500), baseline −5; pytest assets/CSP 173 passed
  - ✅ E2E @core 77 passed — found and fixed: app modals did not open (P.mount missing after the hub-kit split)
  - ✅ E2E 06/29 + Coletor 25/28: 23 passed; ui-sweep --quick: 0 findings

## Closing
- ✅ 9 · Size baseline already empty (no code file > 500 lines). Tighten limits (20 statements, complexity 8), empty baseline, heavy validation, CHANGELOG
  - ✅ limits tightened (complexity 8, 20 statements, 3 nested blocks); 105 noqa marked
  - ✅ agent G: hub ops, admin, gateway, ux, scripts (29)
  - ✅ agent H1: hub api, insight, app/gate/lifespan/routes_login (24)
  - ✅ agent H2: hub connections, auth, vault, storage, graphsim (18)
  - ✅ agent I: SDK + CLI (15)
  - ✅ agent J: sample apps (18)
  - ✅ PLR1702 enforced via explicit preview; 12 nested-block cases refactored (agent K)
  - ✅ bug fixed: auth.json written outside the cross-replica lock (lost update with several replicas)
  - ✅ zero size noqa in the repo; hub-check --all 30/30
  - ✅ heavy validation: E2E 148 passed, ui-sweep full 0 findings, DR/ZD/chaos OK
  - ✅ chaos found: rollout dropped network aliases (502 after hub restart) — fixed + test
  - ✅ DB tests with Postgres: hub 506, SDK 137, apps 42/43/31/29 passed (2 stale tests fixed)
  - ✅ CHANGELOG
  - ✅ owner instance updated (hub_update, zero downtime); healthz/readyz 200, tunnel 302

✅ **Pós-clean-code (tarefas 10–13, 15–17)**

Concluídas em 2026-10-15 (`1d189a8`). Lista final como estava no TASKS.md (pendências abertas seguem no TASKS.md):

## SDK
- ✅ 10 · openapi_of enxerga rotas de include_router
  - ✅ teste red→green; schemas só das rotas /api/
  - ✅ workaround routes.extend removido dos 4 apps; openapi.json idêntico

## Scripts
- ✅ 12 · dash-iso-proof.py: ajuda diz bind, código usa link
  - ✅ ajuda = setup|link|check; comando desconhecido → uso + exit 2 (antes rodava check); teste

## Apps
- ✅ 11 · Links dos apps pela base pública (x-hub-base)
  - ✅ hub_sdk.render.public_base (Renderer.page usa a base do request) + coletor usa o helper
  - ✅ ativos
  - ✅ fornecedores
  - ✅ servicos (inclui _opened)
  - ✅ testes dos 4 apps com Postgres: 43/43/33/33

## Backups
- ✅ 16 · Backups agendados falhavam toda noite desde 07/10 (HUB-WRONG-DB / No such file)
  - ✅ causa: apps em paralelo usavam o mesmo /tmp/hub-role.sql no app-db
  - ✅ tmp_sql(kind): arquivo único por execução (role, admin, restore); teste da corrida
  - ⬜ conferir o próximo backup agendado na :8090
- ✅ 17 · Clone p/ staging do contratos: permission denied to change default privileges (backup #9)
  - ✅ causa: o dump levava os grants da produção (ALTER DEFAULT PRIVILEGES FOR ROLE <papel de prod>); carregar exige ser membro desse papel — falha com admin sem superusuário (ex.: Postgres gerenciado)
  - ✅ pg_dump --no-privileges (o ensure_pg_role refaz os grants do backup a cada execução); teste
  - ✅ reproduzido e corrigido em Postgres descartável; DR na instância isolada OK; backup após restauração OK

## Instância do dono
- ✅ 13 · Deploy dos 4 apps na :8090 (build + up --no-deps, pin keep-20261015)
  - ✅ hub_update (correção do backup) sem parada; 4 apps healthy, readyz 200, status ok
- ▶️ 14 · App de teste do Entra: verificar que nada usa; você apaga no portal
  - ✅ :8090 sem conexões cadastradas (nada depende dele)
  - ⬜ dono apaga no portal do Entra

## Fechamento
- ✅ 15 · E2E dos apps, ui-sweep --quick, CHANGELOG, limpeza
  - ✅ conformidade 4/4; E2E apps 35 passed (07 exige o 06 antes); ui-sweep --quick 0 achados
  - ✅ CHANGELOG; worktrees e instância isolada removidas

✅ **Task 16 — confirmação**

2026-10-15: backup manual dos 4 apps ao mesmo tempo (como o agendador) na :8090, todos "Concluído": ativos 314,3 MB, fornecedores, servicos 529,7 MB, coletor (registros 64–67).
