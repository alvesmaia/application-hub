# Conformidade do contrato de app — 2026-10-05T15:25:26-03:00

Suíte `platform/conformance` (pytest, caixa-preta por HTTP; ver `platform/docs/CONTRATO-APP.md`)
rodando dentro das redes internas de cada app do compose, com o hub-web parado (a suíte confere
o outbox) e o teste de desligamento gracioso (`docker stop -t 10` → saída 0).

| App | Stack | pytest | SIGTERM | Resultado |
|---|---|---|---|---|
| coletor | python | 24 passed, 5 skipped in 1.07s | saída 0 em 0.9 s | ✅ |

Logs e JUnit por app nesta pasta.
