# Pendências pós-clean-code

## SDK
- ✅ 10 · openapi_of enxerga rotas de include_router
  - ✅ teste red→green; schemas só das rotas /api/
  - ✅ workaround routes.extend removido dos 4 apps; openapi.json idêntico

## Scripts
- ✅ 12 · dash-iso-proof.py: ajuda diz bind, código usa link
  - ✅ ajuda = setup|link|check; comando desconhecido → uso + exit 2 (antes rodava check); teste

## Apps
- ▶️ 11 · Links dos apps pela base pública (x-hub-base)
  - ✅ hub_sdk.render.public_base (Renderer.page usa a base do request) + coletor usa o helper
  - ⬜ ativos
  - ⬜ fornecedores
  - ⬜ servicos (inclui _opened)

## Instância do dono
- ⬜ 13 · Deploy dos 4 apps na :8090 (build + up --no-deps, pin keep-20261015)
- ⬜ 14 · App de teste do Entra: verificar que nada usa; você apaga no portal

## Fechamento
- ⬜ 15 · E2E dos apps, ui-sweep --quick, CHANGELOG, limpeza
