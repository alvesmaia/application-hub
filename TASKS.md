# Pendências pós-clean-code

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
