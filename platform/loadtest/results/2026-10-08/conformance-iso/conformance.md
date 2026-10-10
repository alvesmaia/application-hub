# Conformidade do contrato de app — 2026-10-08T19:40:23-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o app-hub-iso-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| ativos | python | 28 passed, 2 skipped in 34.86s | saída 0 em 0.8 s | ✅ |
| fornecedores | python | 25 passed, 5 skipped in 1.26s | saída 0 em 1.1 s | ✅ |
| servicos | python | 26 passed, 4 skipped in 3.66s | saída 0 em 1.0 s | ✅ |
| coletor | python | 25 passed, 5 skipped in 1.34s | saída 0 em 1.1 s | ✅ |

Logs e JUnit por app nesta pasta.
