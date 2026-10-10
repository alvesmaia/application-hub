# Conformidade do contrato de app — 2026-09-28T00:32:08-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | rust | 19 passed in 18.52s | saída 0 em 0.5 s | ✅ |
| fornecedores | rust | 14 passed, 5 skipped in 0.22s | saída 0 em 0.5 s | ✅ |
| servicos | rust | 17 passed, 2 skipped in 3.18s | saída 0 em 0.5 s | ✅ |
| contratos | python | 19 passed in 67.17s (0:01:07) | saída 0 em 0.8 s | ✅ |

Logs e JUnit por app nesta pasta.
