# Conformidade do contrato de app — 2026-09-27T18:39:02-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | rust | 15 passed in 8.34s | saída 0 em 0.7 s | ✅ |
| fornecedores | rust | 12 passed, 3 skipped in 0.24s | saída 0 em 0.6 s | ✅ |
| servicos | rust | 14 passed, 1 skipped in 2.80s | saída 0 em 0.6 s | ✅ |
| contratos | python | 15 passed in 89.21s (0:01:29) | saída 0 em 0.9 s | ✅ |

Logs e JUnit por app: `conformance/run.sh` grava junto do relatório (no CI, artefato `platform-e2e-reports`).
