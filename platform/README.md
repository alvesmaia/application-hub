# platform/

Peças compartilhadas da stack Python. O hub em si está em [`../platform-py/`](../platform-py/); comandos e
guardrails em [`../AGENTS.md`](../AGENTS.md).

| Pasta | Conteúdo |
|---|---|
| `assets/shell` | Front-end do shell servido em `/assets/` (CSS, `hub*.js`, `tokens.css`, fontes, `vendor/`) |
| `assets/web` | JS/CSS das telas do hub (Administração, Operações, Conexões, UX) |
| `assets/i18n` | Catálogo de traduções (`catalog.json`) e `allowlist.json` |
| `config` | Registro de apps (`apps.json`, `apps.compose.json`), sementes de variáveis, `hub.example.toml` |
| `demo-apps-py` | Apps oficiais em Python: `ativos`, `fornecedores`, `servicos`, `coletor` |
| `demo-apps` | CSS e fragmentos compartilhados dos apps de demonstração |
| `sdk-python` | SDK `hub_sdk` |
| `conformance` | Suíte de conformidade caixa-preta (rodar por `platform-py/scripts/conformance.sh`) |
| `docs` | `CONTRATO-APP.md`, `DESIGN-TOKENS.md`, `A11Y-TECLADO.md`, OpenAPI |
| `js-tests` | Testes do JS do shell (`node --test js-tests/`) |
| `tests-e2e` | Playwright e varredura de UI (`ui-sweep/`) |
| `loadtest` | `py-dr.mjs`, `py-resilience.mjs`, `py-build-live.mjs`, `mcp-client`; resultados históricos em `results/` |
| `infra` | Imagens auxiliares (MinIO/mc, OTel Collector) |
| `samples` | Pacotes `.hubapp` de exemplo |
| `scripts` | Paleta/tema (`gen-dark-theme.mjs`), ícones PWA, MinIO, túnel de dev, pacotes de exemplo |
