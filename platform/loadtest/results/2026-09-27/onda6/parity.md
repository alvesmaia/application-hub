# Paridade `hub dev` × produção — fornecedores (2026-09-27T13:57:43.710Z)

Gerado por `tests-e2e/parity/parity.mjs`. Produção = compose em http://localhost:8080 (login do admin de
demonstração); hub dev = `hub dev --role admin` nativo (sem Docker) com `hub-demo-apps` só com
`fornecedores` e o serviço externo simulado nativo (`HUB_EXTAPI_URL`). Normalização: marca, usuário
(nome/iniciais/papel), sino de alertas e menu do usuário (só na plataforma), faixa do hub dev,
`?v=` dos assets, horários.

Resultado: **idêntico** em 19 verificações.

| Tipo | Item | Igual | Detalhe |
|---|---|---|---|
| shell | `/fornecedores/dashboard` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/fornecedores/dashboard` | ✅ | HTTP 200/200 · idêntico |
| shell | `/fornecedores/indicators` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/fornecedores/indicators` | ✅ | HTTP 200/200 · idêntico |
| shell | `/fornecedores/analysis` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/fornecedores/analysis` | ✅ | HTTP 200/200 · idêntico |
| shell | `/fornecedores/reports` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/fornecedores/reports` | ✅ | HTTP 200/200 · idêntico |
| shell | `/fornecedores/settings` | ✅ | HTTP 200/200 · idêntico após normalizar |
| fragmento | `/_frag/fornecedores/settings` | ✅ | HTTP 200/200 · idêntico |
| asset | `/assets/logo.svg` | ✅ | HTTP 200/200 · 417 B · sha256 07c6df24c9f18530 |
| asset | `/assets/styles.css` | ✅ | HTTP 200/200 · 55664 B · sha256 3f41ddefa13fd144 |
| asset | `/assets/hub.css` | ✅ | HTTP 200/200 · 4710 B · sha256 6cff98813ce1694c |
| asset | `/assets/hub.js` | ✅ | HTTP 200/200 · 18578 B · sha256 2e75f5a6651d9e2e |
| asset | `/_frag/fornecedores/assets/demo.css` | ✅ | HTTP 200/200 · 3135 B · sha256 626d27f0f26e3872 |
| asset | `/_frag/fornecedores/assets/app.css` | ✅ | HTTP 200/200 · 35538 B · sha256 5b2ab0df3dd9f8b3 |
| asset | `/_frag/fornecedores/assets/chart.js` | ✅ | HTTP 200/200 · 15627 B · sha256 851290c0eebb7742 |
| asset | `/_frag/fornecedores/assets/interact.js` | ✅ | HTTP 200/200 · 4859 B · sha256 4c80a81fa87896dd |
| comportamento | `POST lookup FOR-0284` | ✅ | HTTP 200/200 · mesmo resultado |

