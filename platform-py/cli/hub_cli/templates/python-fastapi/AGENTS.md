# AGENTS.md — __APP_NAME__ (`__APP_ID__`)

App da **Application Hub**. Guia para agentes de código (Claude Code, Codex…) e pessoas.

## Stack

Python 3.12 (`uv`), FastAPI, Jinja2 (SSR), `hub_sdk` (SDK do hub, em `vendor/`), SQLAlchemy Core + Alembic
(`hub_sdk.db`: SQLite em `.hub/dev.db` no dev, Postgres no hub — mesmo código), Pydantic v2 nas bordas.
Qualidade: ruff (inclui regras `S`), ty, pytest + pytest-cov (piso de 80%).

## Comandos

| Comando | O que faz |
|---|---|
| `hub dev` | Hub local leve (shell, gateway, identidade simulada) + app com recarga em `http://localhost:4173/apps/__APP_ID__` — sem Docker, sem Postgres |
| `hub check` | ruff, formatação, ty, pytest + cobertura, manifesto, conformidade local e `openapi.json` |
| `hub deploy --env staging` | Empacota e envia ao hub (produção: `--env production`, fica pendente de aprovação) |
| `hub pack` | Gera o `.hubapp` para envio manual na tela Implantações |

Primeira vez: `uv sync`. Rode `hub check` antes de todo commit.

## Convenções

- Rotas sob `/__APP_ID__/…`; o navegador vê `/apps/__APP_ID__/…`. **Todo link/`action`** usa `{{ base }}` (vem do
  cabeçalho `x-hub-base`) — nunca `/__APP_ID__/…` cru.
- Telas = fragmentos HTML com o workspace (`templates/*.html` estendem `workspace.html` do SDK) e as classes do hub
  (`app-page-header`, `content-panel`, `data-table`, `form-field`, `primary-button`, `metric-card`…). Cores só por
  tokens CSS do hub (`var(--…)`) em `assets/app.css`.
- Comportamentos de tela só por atributos `data-hub-*` (contrato §25: modal, confirmação, filtro, toast, poll…).
- Formulário `method="post"`: o hub envia por XHR e troca o workspace pela resposta (devolva a página).
- Entradas (formulário e API) validadas por modelos Pydantic. API JSON em `/__APP_ID__/api/v1/*`, cada rota com
  `operation_id` (vira ferramenta MCP); erros RFC 9457 (`hub_sdk.api.problem`); `require_user` exige identidade.
- Esquema do banco só por migração Alembic em `src/__PKG__/migrations/versions/` (aplicadas na subida).
- O que o app precisa do hub vai em `manifest.json` → `requires` (`database`, `storage`, `variables`, `secrets`).
- Arquivos: `hub_sdk.storage.connection("<nome>")` (no `hub dev` grava em `.hub/storage/`).
- Prontidão: `GET /__APP_ID__/_hub/ready` (já instalado pelo `HubSdk`).

## Dashboards (BI leve)

- `hub_sdk.dashboards` (extra `hub-sdk[dashboards]`; template pronto: `hub project create <id> --template dashboard`):
  `Dataset` (SQL com parâmetros `:nome` contra uma conexão SQL), `Param` (filtros da query string, validados),
  `Dashboard.load` (cache por combinação de filtros, `ttl`), `kpi`/`kpis`, `line_chart`/`area_chart`/`bar_chart`/
  `pie_chart`/`donut_chart` (SVG pronto no servidor, cores por tokens — claro/escuro), `table` (filtro e paginação
  do hub), `filters_form` (`<form method="get">`), `export_links` + `export_response` (CSV/XLSX).
- Fonte dos dados: `hub_sdk.sql.connection("<nome>")` — no hub, conexão SQL de Administração › Conexões
  (PostgreSQL, MySQL/MariaDB, SQL Server) vinculada ao app × ambiente, pelo broker (somente leitura, limite de
  linhas/tempo, auditoria sem dados; o app nunca vê a senha); no `hub dev`, `.hub/connections.toml` (cópia de
  `dev/connections.toml`; SQLite de exemplo de `dev/<nome>.sql`). O banco do próprio app: `hub_sdk.sql.from_engine`.
- SQL portável e parametrizado (nada de f-string com valor de usuário). O CSS dos dashboards (`dash.CSS`) vai
  junto do `app.css` servido pelo app. Exportação por link `download` para `/_frag/__APP_ID__/…`.
- Guia: `DASHBOARDS.md` (no hub: Desenvolvedores).

## Proibido

- JavaScript próprio, `<script>` inline, `on*=` ou `style=` com cores literais.
- Segredos no código, em `.env.example`, em HTML, logs ou respostas da API.
- Links sem a base pública, `<html>/<head>/<body>` nos fragmentos, cookies próprios `hub_*`.
- Alterar o esquema sem migração; SQL montado com f-string (use SQLAlchemy Core).
- Rodar como root ou escrever fora de `/scratch` na imagem.

Contrato completo: `CONTRATO-APP.md` (no hub: Desenvolvedores → Contrato do app).
