# Paridade `hub dev` × produção — contratos (2026-09-27T19:40:53.956Z)

Gerado por `tests-e2e/parity/parity.mjs`. Produção = compose em http://localhost:8080 (login do admin de
demonstração); hub dev = `hub dev --role admin` nativo (sem Docker) com `python -m contratos` (uvicorn, venv, Job Object no Windows) e um Postgres descartável sem carga inicial. Normalização: marca, usuário
(nome/iniciais/papel), sino de alertas e menu do usuário (só na plataforma), faixa do hub dev,
`?v=` dos assets, horários.

Resultado: **idêntico** em 14 verificações.

| Tipo | Item | Igual | Detalhe |
|---|---|---|---|
| shell | `/contratos/dashboard` | ✅ | HTTP 200/200 · idêntico após normalizar |
| shell | `/contratos/contracts` | ✅ | HTTP 200/200 · idêntico após normalizar |
| shell | `/contratos/settings` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/contratos/_skeleton` | ✅ | HTTP 200/200 · idêntico |
| fragmento | `/_frag/contratos/settings` | ✅ | HTTP 200/200 · idêntico |
| asset | `/assets/logo.svg` | ✅ | HTTP 200/200 · 417 B · sha256 07c6df24c9f18530 |
| asset | `/assets/styles.css` | ✅ | HTTP 200/200 · 55664 B · sha256 3f41ddefa13fd144 |
| asset | `/assets/hub.css` | ✅ | HTTP 200/200 · 4710 B · sha256 6cff98813ce1694c |
| asset | `/assets/hub.js` | ✅ | HTTP 200/200 · 22201 B · sha256 1478a5aaafb2f42d |
| asset | `/_frag/contratos/assets/demo.css` | ✅ | HTTP 200/200 · 3135 B · sha256 626d27f0f26e3872 |
| asset | `/_frag/contratos/assets/app.css` | ✅ | HTTP 200/200 · 783 B · sha256 e2e1afe9d00e2785 |
| asset | `/_frag/contratos/assets/chart.js` | ✅ | HTTP 200/200 · 15627 B · sha256 851290c0eebb7742 |
| asset | `/_frag/contratos/assets/interact.js` | ✅ | HTTP 200/200 · 4859 B · sha256 4c80a81fa87896dd |
| comportamento | `POST /contratos/contracts (inválido)` | ✅ | HTTP 422/422 · mesma resposta |

