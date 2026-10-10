# Validação local — onda 10 (Conexões / SharePoint) — 28/09/2026

CI do GitHub Actions bloqueado (billing): tudo rodado nesta máquina (Windows 11, Docker Desktop),
compose com `--profile sharepoint-sim` e `HUB_GRAPH_URL`/`HUB_GRAPH_LOGIN_URL` apontando para o simulador.

| Checagem | Resultado |
|---|---|
| `cargo fmt --all --check` · `cargo clippy --workspace --all-targets -D warnings` | ✅ · 0 avisos |
| `cargo test --workspace` | ✅ 67 suítes, 0 falhas (inclui `hub-storage` 24 + 9 com o simulador, `hub-web --test connections` 4, `broker::tests`, `connections::tests` 5, `hub-demo-apps --test routines` 7) |
| DB: `hub-sdk --test db` · `hub-demo-apps --test db` · `hub-ops --lib` (ignored) | ✅ 16 · 7 (inclui 503 transitório em ativos/servicos) · 4 |
| Docker: `hub-runtime --test docker` | ✅ 3 |
| JS `node --test js-tests/` | ✅ 18 |
| Python `ruff check`/`format --check` · `mypy --strict` (sdk, contratos) · `pytest` (com DB) | ✅ · ✅ · sdk 91, contratos 28 |
| `cargo audit` (container `rust:1-bookworm`, volume `hub-audit-cache`) | ✅ 0 vulnerabilidades (473 crates) |
| `cargo deny check` | ✅ advisories/bans/licenses/sources ok |
| Conformidade `conformance/run.sh` | ✅ 4/4 (item 22: fornecedores round-trip no S3; contratos 503 com o broker parado) |
| Playwright `npm run e2e` | ✅ **56/56** (53 anteriores + 3 da onda 10; axe inclui `/admin/connections` em 2 temas × 2 idiomas) |
| DR `node loadtest/dr.mjs` | ✅ todas as verificações (`dr.md`) |

Correções feitas durante a validação: contraste AA na tela Conexões e no contador do sino
(`.hub-notif-count` sobre `--c-red-40`) — o axe acusou com alertas não lidos presentes.
Regressão visual não reexecutada (a cor do contador do sino mudou; referências podem precisar de revisão).
