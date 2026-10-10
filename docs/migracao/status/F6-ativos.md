# F6 — Ativos em Python (status)

Feito em `platform/demo-apps-py/ativos/` (FastAPI + `hub_sdk`; port do `hub-demo-apps` com `HUB_DEMO_APP=ativos`):
- Mesmas rotas sob `/ativos` (dashboard, assets, movements, reports, settings, `_skeleton`, `assets/{demo,app}.css`, `itens/*` de cadastro, importação, exportação CSV/XLSX, modelo), `_hub/*` e `_hub/ready` do SDK, `/ativos/api/v1/*` + `/ativos/api/openapi.json` (`openapi.json` do pacote gerado por `python -m ativos openapi`), MCP interno gerado do OpenAPI, busca federada, flag `novo-resumo`, saga "cadastro documentado" (+ promoção de uploads se a conexão `ativos-docs` existir).
- Markup idêntico: seções estáticas = os fragmentos do Rust (copiados; sem JS), telas dinâmicas reescritas string a string (`views.py`), i18n por tradução do HTML (`i18n.py`, catálogo `i18n.json` do Rust), CSS teal do Rust (`assets/app.css`, claro/escuro).
- Banco: mesmo esquema (`assets` + tabelas do SDK), seed idêntico (`python -m ativos seed`, 300 mil × `HUB_SEED_SCALE`, ou automático após a migração; `ATIVOS_SEED_ROWS` sobrescreve). Importação lê com python-calamine, exportação escreve com openpyxl write-only; relatório de erros em CSV como no Rust.
- Imagem: `demo-apps-py/ativos/Dockerfile` (contexto `platform/`), `requirements.lock` do contratos (mesmas dependências); build ok, saudável, SIGTERM em 1 s com saída 0.

Verificação: `ruff check/format`, `ty check src tests`, `pytest` (43 testes, ~85% de cobertura; com Postgres descartável `HUB_DB_TEST_URL`); conformidade `platform/conformance` contra o app local: 27 passam, 2 pulados, 1 falha (`test_search_contract`, ver gaps); paridade de HTML contra o gateway do hub Rust (somente leitura): seções, skeleton, 404, lista filtrada/ordenada, `?edit`, `?new`, `?import`, estados vazios, `demo.css`/`app.css` idênticos byte a byte após normalizar espaços.

Gaps:
- `test_search_contract` espera URL `/ativos/...`; o app (como o Rust) devolve `/apps/ativos/...` (Onda 13; o SDK aceita os dois) — a suíte está defasada, falharia também no app Rust.
- Fora desta pasta (não tocados): `samples/src/ativos/{manifest,openapi}.json`, compose, `ci-local`, `Dockerfile.ativos`/serviço `hub-seed` ainda são os do Rust.
- Não comparados contra o Rust vivo (escrevem no banco dele): POSTs de cadastro/edição/exclusão/importação/exportação — cobertos por testes e pelo código-fonte; idioma en/es testado por unidade (casos do `i18n.rs`) e por resposta, não ao vivo.
- Diferenças deliberadas: a aba do `.xlsx` é carregada em memória pelo calamine (teto de 300 MB descomprimidos, `ATIVOS_IMPORT_MAX_SHEET_MB`; Rust lê em streaming até 2 GiB); API de edição mescla valores ausentes com o formato de tela (o Rust reinterpretava centavos como reais); validação do corpo da API devolve 422 (Rust: 400 para `version` ausente).
