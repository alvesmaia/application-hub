# Conformidade do contrato de app — 2026-09-28T04:42:17-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | rust | 21 passed, 1 skipped in 10.95s | saída 0 em 0.5 s | ✅ |
| fornecedores | rust | 16 passed, 6 skipped in 0.25s | saída 0 em 0.5 s | ✅ |
| servicos | rust | 19 passed, 3 skipped in 2.52s | saída 0 em 0.6 s | ✅ |
| contratos | python | 22 passed in 10.06s | saída 0 em 0.8 s | ✅ |

Logs e JUnit por app nesta pasta.
