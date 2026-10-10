# Conformidade do contrato de app — 2026-09-27T19:31:10-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | rust | 16 passed in 10.67s | saída 0 em 0.6 s | ✅ |
| fornecedores | rust | 12 passed, 4 skipped in 0.23s | saída 0 em 0.8 s | ✅ |
| servicos | rust | 15 passed, 1 skipped in 3.23s | saída 0 em 0.8 s | ✅ |
| contratos | python | 16 passed in 63.84s (0:01:03) | saída 0 em 0.8 s | ✅ |

Logs e JUnit por app nesta pasta.
