# F2a - Inicio, Aplicacoes, Status

Codigo: `platform-py/hub/ux/` (`routes.py`, `store.py`, `catalog.py`, `status.py`, `texts.py`), templates `home|applications|status|status_public.html`, `texts.json`/`icons.json` extraidos do hub Rust (`i18n.rs`, `icons.rs`).

- `/` Inicio: saudacao, onboarding (estado por usuario, passos por permissao), widgets Favoritos/Inbox/Recentes/Status/Atalhos, Personalizar (layout por usuario e padrao por funcao).
- `/applications`: cartoes com situacao, versao implantada (`data/deployments.json`, stub ate F5; cai no manifesto), favoritos, overlay de stack (manifesto `stack`/`runtime`/`api`/`mcp`), "Outras aplicacoes" + pedir acesso (botao).
- `/status`: banner, apps x Producao, barras de 30 dias (rotulos GMT-3 como o Rust), incidentes; versao publica sem sessao quando `settings.status_public` (apps em `status_public_apps`).
- APIs (mesmo JSON do Rust): `GET/PUT /api/me/prefs`, `PUT /api/me/onboarding`, `POST /api/me/onboarding/step`, `PUT|DELETE /api/me/home`, `PUT /api/admin/home-layouts/{role}`, `PUT /api/me/favorites/{app}`, `POST /api/me/analytics` (204, so aceita).
- Recentes e passo "abrir app" alimentados no middleware (`/apps/*`, `/_frag` com `x-hub-nav`). Estado em `data/ux.json`.
- Ajustes colaterais: papeis padrao iguais ao Rust (Product Owner, Tech Lead [1,0,1,1,1,0]); iniciais do avatar e paleta Ctrl+K (apps primeiro) como o Rust; `Registry.entries()`.

## Paridade (`compare.mjs`, Rust :8080 x Py :8090)
| rota | status | DOM js/cru (falta/extra) | pixels 1440/390 |
|---|---|---|---|
| `/` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/applications` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/status` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/login` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |

Exclusoes novas em `ignore.json` (dado variavel, justificado em `_why`): itens do sino/widget Inbox (Inbox = F2b), Recentes, checklist do onboarding (cobertos por pytest), barras/uptime/SLO e linha Staging do status (telemetria/staging = F5), lista de incidentes. Harness: ignoradas tambem nos pixels; `animations: disabled`.
Seed de dev (volume do hubpy): `data/deployments.json` com as versoes que o Rust mostra (coletor 1.7.9, fornecedores 1.9.233754, ...).

## Qualidade
`check.sh` com Postgres descartavel: ruff, format, ty, `uv audit` ok; 68 passed; cobertura 86.2% (hub.ux 95%+). Sem banco, 5 testes pulam e o total fica em 79% (state 40%) - mesmo comportamento da F1f.

## Lacunas
- Inbox real (itens, sino, `/api/me/inbox*`, `/api/me/stream`, busca `/api/me/search`) e pedidos de acesso (`/api/me/access-requests`): F2b. Widget Inbox renderiza vazio; botao "Solicitar acesso" sem endpoint.
- Telemetria/SLO/incidentes reais e botoes de incidente (`/api/admin/incidents*`): F5. Status mostra "sem dados".
- Analytics de uso (`Ev::*`), i18n por usuario (so pt-BR resolvido; textos ja trazem en/es), tema por preferencia no servidor.
- `ux.json` em arquivo local (sem lock no state DB; um processo).
