# platform/

Shared parts of the Python stack. The hub itself lives in [`../platform-py/`](../platform-py/); commands and
guardrails are in [`../AGENTS.md`](../AGENTS.md).

| Folder | Contents |
|---|---|
| `assets/shell` | Shell front end served at `/assets/` (CSS, `hub*.js`, `tokens.css`, fonts, `vendor/`) |
| `assets/web` | JS/CSS for the hub screens (Administration, Operations, Connections, UX) |
| `assets/i18n` | Translation catalog (`catalog.json`) and `allowlist.json` |
| `config` | App registry (`apps.json`, `apps.compose.json`), variable seeds, `hub.example.toml` |
| `demo-apps-py` | Official Python apps: `ativos`, `fornecedores`, `servicos`, `coletor` |
| `demo-apps` | Shared CSS and fragments for the demo apps |
| `sdk-python` | `hub_sdk` SDK |
| `conformance` | Black-box conformance suite (run via `platform-py/scripts/conformance.sh`) |
| `docs` | `CONTRATO-APP.md`, `DESIGN-TOKENS.md`, `A11Y-TECLADO.md`, OpenAPI |
| `js-tests` | Tests for the shell JS (`node --test js-tests/`) |
| `tests-e2e` | Playwright and UI sweep (`ui-sweep/`) |
| `loadtest` | `py-dr.mjs`, `py-resilience.mjs`, `py-build-live.mjs`, `mcp-client`; historical results in `results/` |
| `infra` | Auxiliary images (MinIO/mc, OTel Collector) |
| `samples` | Example `.hubapp` packages |
| `scripts` | Palette/theme (`gen-dark-theme.mjs`), PWA icons, MinIO, dev tunnel, example packages |
