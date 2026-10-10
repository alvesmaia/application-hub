# Conformidade do contrato de app — 2026-09-28T16:56:56-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | rust | 21 passed, 2 skipped in 61.39s (0:01:01) | saída 0 em 0.7 s | ✅ |
| fornecedores | rust | 17 passed, 6 skipped in 0.27s | saída 0 em 0.5 s | ✅ |
| servicos | rust | 19 passed, 4 skipped in 3.35s | saída 0 em 0.5 s | ✅ |
| contratos | python | 23 passed in 27.87s | saída 0 em 0.7 s | ✅ |

Logs e JUnit por app nesta pasta.
