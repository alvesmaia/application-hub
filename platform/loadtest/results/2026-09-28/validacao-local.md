# Validação local completa — substitui CI bloqueado (GitHub Actions/billing)

Repo: raiz do repositório, branch `stack/rust-topcoat`, código em `platform/`.
Data: 2026-09-28. Máquina: Windows 11, Docker Desktop.

## Resultado por etapa

| # | Checagem | Resultado | Detalhe |
|---|---|---|---|
| 1 | `cargo fmt --all --check` | ✅ PASS | sem diffs |
| 1 | `cargo clippy --workspace --all-targets -- -D warnings` | ✅ PASS | 0 warnings |
| 1 | `cargo test --workspace` | ✅ PASS | 60 suites, 0 falhas |
| 1 | `cargo build --release --workspace` | ✅ PASS | build completo |
| 1 | `cargo audit` | ✅ PASS | (reexecução) em container `rust:1-bookworm` (platform/ montado só leitura, cache no volume `hub-audit-cache`): cargo-audit 0.22.2, 1273 avisos RustSec, 429 crates — **0 vulnerabilidades, 0 avisos** |
| 1 | `cargo deny check` | ✅ PASS | (reexecução) mesmo container, cargo-deny 0.20.2: `advisories ok, bans ok, licenses ok, sources ok`; só 22 `warning[duplicate]` (versões duplicadas, ex.: `zip` 2.4 × 8.6 — política `multiple-versions = "warn"`) |
| 2 | DB-gated `hub-sdk --test db` | ✅ PASS | 15/15 (Postgres `hub-testdb` descartável) |
| 2 | DB-gated `hub-demo-apps --test db` | ✅ PASS | 6/6 |
| 2 | DB-gated `hub-ops --lib --ignored` | ✅ PASS | 4/4 |
| 3 | Docker-gated `hub-runtime --test docker` (`DOCKER_AVAILABLE=1`) | ✅ PASS | 3/3 |
| 4 | JS `node --test js-tests/` | ✅ PASS | 18/18 |
| 5 | Python `ruff check` (sdk-python, demo-apps-py/contratos, conformance) | ✅ PASS | sem apontamentos |
| 5 | Python `ruff format --check` | ✅ PASS | 43 arquivos já formatados |
| 5 | `mypy --strict` (sdk-python) | ✅ PASS | 17 arquivos, sem erros |
| 5 | `mypy --strict` (contratos) | ✅ PASS | 8 arquivos, sem erros |
| 5 | `pytest` sdk-python (com DB) | ✅ PASS | 57 passed |
| 5 | `pytest` contratos (com DB) | ✅ PASS | 23 passed |
| 5 | lockfile de produção `pip install --require-hashes -r requirements.lock` | ✅ PASS | 20 pacotes, hashes conferidos, venv descartável |
| 6 | `docker compose build` (todas as imagens) | ✅ PASS | build completo, sem cache issues |
| 6 | `docker compose up -d` + saudável | ✅ PASS | 8/8 containers healthy |
| 7 | Conformidade (`conformance/run.sh`) | ✅ PASS | 4/4 apps (ativos, fornecedores, servicos, contratos) |
| 7 | Playwright suíte completa (`npm run e2e`) | ✅ PASS | **53/53**, incl. axe |
| 7 | Regressão visual (light+dark) | ✅ PASS | (reexecução) **24/24** no hub nativo (`scripts/demo.sh`, `HUB_DATA_DIR` novo), que é o ambiente das referências (`playwright.visual.config.ts`). A falha de `admin-variaveis (escuro)` só ocorre contra o **compose**: lá há o 4º app (`Contratos`, aba extra) e as connection strings provisionadas (`DATABASE_URL_*`), que empurram colunas — diferença real de **dados**, não de UI nem baseline velha (a versão clara também difere, mas fica abaixo do limiar de cor por pixel no fundo claro). Nenhuma referência regravada. |
| 7 | DR com backups cifrados (`loadtest/dr.mjs`) | ✅ PASS | todas as 13 verificações OK, incl. pacotes `.hubenc`, credenciais fora do env dos containers de tarefa |
| 7 | Zero-downtime (`loadtest/zero-downtime.mjs --rps 50`) | ✅ PASS | 3 deploys + rollback, blue/green, **0** respostas da versão antiga após a virada em todas as trocas |
| 7 | Caos Rust (`loadtest/chaos/chaos.mjs --hub-kills 1 --db-restarts 1`) | ✅ PASS | (reexecução) 16/16: 422 eventos de auditoria exatamente uma vez + 1 aviso `notification` exatamente uma vez na Inbox; 420/420 cadastros, `{"criado":417,"já concluída":3}` (`chaos.md`) |
| 7 | Caos Python (`loadtest/chaos/chaos-contratos.mjs`) | ✅ PASS | (reexecução) 17/17: 422 auditoria + 1 aviso na Inbox, exatamente uma vez cada; pico 281 MiB (`chaos-contratos.md`) |
| 8 | `docker compose down` | ✅ PASS | stack removida |
| 8 | Limpeza de containers/redes de staging | ✅ N/A | nenhum leftover de staging (`hub-app-*__staging`) neste ciclo |

## Fix commits

Ver o histórico do branch (`fix(rust-topcoat): caos …`). Resumo da reexecução dirigida:

- **Caos — "1 fora da auditoria"**: causa raiz no **script**, não no hub. Desde a onda 9 o outbox também grava avisos da Inbox (`kind = notification`, o "Importação … concluída" de `final_events`); `hub-admin/src/outbox.rs` os entrega à Inbox (`NOTICE_SINK`), não à trilha. Os scripts comparavam **todas** as linhas do outbox com a auditoria: 423 = 422 `audit` + 1 `notification`. Confirmado na reexecução: 0 eventos `audit` fora da trilha; o único "faltante" era o aviso, presente uma vez na Inbox. Correção: `chaos.mjs`/`chaos-contratos.mjs` conferem por tipo (auditoria × trilha; `notification`/`approval_request` × Inbox pelo link do aviso; tipo desconhecido falha), exatamente uma vez em cada.
- **Caos — "HTTP 500" intermitente na rajada (Rust)**: numa reexecução, 1 cadastro recebeu 500 durante a queda do `hub-db` (a escrita não foi perdida nem duplicada, mas o cliente não repete 500). Causa: o `create` do `ativos` mapeava qualquer erro do Postgres na transação (conexão encerrada pelo servidor) para 500. Correção: `hub_sdk::db_transient` (sem SQLSTATE/E-S, classe 08, 57P01–57P03, 53300, 40001, 40P01) → 503 + `Retry-After`; teste de banco `terminated_connection_is_transient` (hub-sdk `--test db` 16/16) e unitário `transient_sqlstates`. O app Python já respondia 503.

## Preocupações

1. ~~`cargo audit`/`cargo deny` locais~~ — executados em container Docker (ver tabela). Localmente continua faltando `dlltool` (mingw-w64).
2. Regressão visual: rode **no hub nativo** (`scripts/demo.sh`, dados novos), não no compose — no compose `admin-variaveis (escuro)` falha por dados diferentes (app Contratos e connection strings), não por UI.
3. ~~Caos "1 evento fora da auditoria"~~ — resolvido (artefato de contagem do script; ver Fix commits).
4. Ordem de execução importa para a regressão visual: rodá-la **depois** da suíte E2E completa (que muda estado — inbox, tour, notificações) no mesmo stack produz falsos positivos. Documentando aqui para quem for reproduzir: rode a regressão visual logo após subir o stack com dados novos, antes de qualquer outra suíte que grave estado.
