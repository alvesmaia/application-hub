# AGENTS.md

Guia para agentes de código (Claude Code, Codex e outros) que trabalham neste repositório.

## Branches e onde trabalhar

- **`main`**: stack oficial — o hub em **Python** (`platform-py/`) e os apps Python (`platform/demo-apps-py/`). Desde 2026-10-07 serve a instância de produção do dono. Também guarda o mockup estático original (raiz: `index.html`, `js/`, `css/`) e a documentação de produto (`docs/PLAN.md`, `docs/FAQ-ARQUITETURA.md`).
- **`stack/python`**: branch de trabalho da migração; acompanha a `main`.
- **`stack/rust-topcoat`**: hub Rust + Topcoat **arquivado** (referência de paridade). O código Rust foi removido da `main` (tarefa #20); consulte a branch só como referência — não evoluir.
- **Leia primeiro:** `docs/PLANO-MIGRACAO-PYTHON.md` (decisões fixas da stack), `docs/migracao/status/*.md` (o que cada etapa entregou e as lacunas — `F7a.md` lista os resíduos conhecidos), `docs/CONTRATO-APP.md` (contrato HTTP dos apps, em `platform/docs/`), `docs/HANDOFF.md`.

## Stack (decisões fixas)

Python 3.12 gerenciado por `uv`; FastAPI + Jinja2 (SSR, mesmo HTML do Topcoat), asyncpg, httpx, Pydantic v2 + pydantic-settings, SDK `docker`. Qualidade: **ruff** (lint e formatação, incluindo as regras `S` do bandit), **Astral ty** (tipos), **pytest + pytest-cov** (piso de 80% no núcleo), **`uv audit`**. Front-end (CSS, `hub*.js`, fontes, `catalog.json`) é o mesmo do hub Rust e fica em `platform/assets/shell` (shell) e `platform/assets/web` (telas do hub); registro de apps em `platform/config/` — não reescrever. Apps oficiais só em Python (SDK `hub_sdk` em `platform/sdk-python`); o SDK Rust foi descontinuado.

## Comandos (em `platform-py/`)

- Checagem completa: `uv run hub-check` (ruff, ruff format, ty, `uv audit`, pytest com cobertura; `--fast` = só ruff/formato/ty; `--all` = + cli, SDK e cada app). Testes de banco usam um Postgres descartável (ver `docs/migracao/status/F1a.md`).
- Pilha: projeto compose `app-hub` (imagem selada `app-hub-web`; dev.sh up monta o código) — `app-hub-web` em `127.0.0.1:8090`, `app-hub-db`, `app-hub-objstore` (MinIO), apps `app-hub-app-<id>` (`docker-compose.apps.yml`), segredos de dev em `.dev.env` (gitignored; nunca commitar nem imprimir).
- Instalação do zero / outra instância no mesmo host: `bash scripts/install.sh [--project app-hub-x --port 8095 --subnet 10.241] [--no-demo-apps]` (compose parametrizado por `HUB_PROJECT`/`HUB_PORT`/`HUB_SUBNET`; apps de exemplo no perfil `demo-apps`). Versão: `hub/__init__.py` (`/healthz`), `CHANGELOG.md`, `scripts/release.sh <versão>`; licença Apache-2.0 (`NOTICE`, `TRADEMARKS.md`).
- `bash scripts/dev.sh up|down|stop|status|logs|reload-on|reload-off|obs-up|obs-down` — modo dev (uvicorn `--reload`, recarga SSE `/_dev/reload`), perfil observability (OTel Collector + Jaeger).
- Simulador SharePoint: `docker-compose.graphsim.yml` (perfil `sharepoint-sim`, `app-hub-graphsim`).
- Conformidade dos apps: `bash scripts/conformance.sh`. CLI: `platform-py/cli` (`hub pack`, `hub mcp`, `hub dev`).
- Paridade de HTML/pixels com o hub Rust (só com a branch `stack/rust-topcoat` no ar): `MSYS_NO_PATHCONV=1 node parity/compare.mjs <rotas>` (`--click` para modais; `ignore.json` só com `_why`).
- E2E e varredura de UI contra a :8090: `platform/tests-e2e` (`npx playwright test`, `node ui-sweep/run.mjs [--quick]`). **Toda mudança de UI precisa passar no ui-sweep.** Resiliência: `platform/loadtest/py-dr.mjs`, `py-resilience.mjs` (só tocam `app-hub-*`).

## Guardrails

- **Hooks (clone novo):** `cd platform-py && uv sync && uv run pre-commit install --allow-missing-config --hook-type pre-commit --hook-type pre-push`. Pre-commit: ruff, ruff format, ty, guarda de segredos (`platform-py/scripts/secret_guard.py`: `.dev.env`, chaves/certificados, `hubt_`, credenciais do cloudflared), arquivos > 1 MB. Pre-push: `uv run hub-check` completo. Nunca `--no-verify`.
- **`uv run hub-check [--fast|--all]` verde é obrigatório antes de commit/push** (funciona em PowerShell e Git Bash; `--all` inclui `platform-py/cli`, `platform/sdk-python` e cada app em `platform/demo-apps-py/*`). `scripts/check.sh` é só um atalho. Mudou app: também o próprio check do app + `bash scripts/conformance.sh`.
- **Pydantic v2 nas bordas:** formulários, entradas de API e escritas da Administração são validados por modelos Pydantic, não por `dict` cru.
- **Segredos nunca** em HTML, API, log, auditoria nem git (`.dev.env` fica fora do git).
- **Toda mudança de UI passa no ui-sweep**; texto novo nos 3 idiomas (pt-BR, en, es).
- **Validação:** leve durante o dia (`--fast`, testes do afetado, ui-sweep `--quick`); pesada uma vez ao fim do dia (E2E completo, DR, caos).

## Arquitetura do hub Python (`platform-py/hub`)

`auth` (sessões, RBAC, tokens), `vault` (cofre), `state` (Postgres: sessões, jobs, liderança), `admin` (lojas + auditoria em cadeia), `gateway` (proxy `/apps/{id}`, `/_frag/{id}`, identidade assinada `x-hub-user`, `x-hub-base`, CSP, WebSocket), `ux` (Início, busca, Inbox, configurações, i18n), `connections`/`storage` (SharePoint/S3 e broker — credencial nunca em HTML/API/log/auditoria), `insight` (logs, flags, SLOs, observabilidade, OTLP), `ops` (implantações, build de imagem, blue/green, staging, supervisor, backups), `api` (tabela única que gera `/api/v1`, OpenAPI e ferramentas MCP; OAuth 2.1 do MCP). Templates em `hub/web/templates`. Menu (21 telas): `hub/web/nav.json` (`also` = outras rotas que acendem o item) e abas em `hub/ux/shell.py` (`TAB_SETS`: Usuários/Funções `?tab=roles`, Logs/Auditoria, Variáveis/Conexões; Minha conta = `/account`, `/account/security`, `/account/access`); Observabilidade é seção da Saúde, uso (MAU) e rotações de chaves ("Segurança e chaves") ficam em Configurações; rotas antigas respondem 308.

## Regras

- O hub só gerencia contêineres `app-hub-*` (trava em `hub/ops/runtime.py`). Nunca tocar em contêineres de outros projetos nem encerrar processos por nome de imagem.
- Túnel público = contêiner `apphub-devtunnel` (config `platform/.tunnel/config-standalone.yml`): **nunca parar/remover sem pedido do dono**.
- URLs de apps: `/apps/{id}/…`; app monta links com a base pública (`x-hub-base`, `{{ base }}`), nunca `/{id}/…` cru. Apps sem JavaScript próprio (comportamentos via `data-hub-*` no `hub-kit.js`); prontidão obrigatória `GET /{app}/_hub/ready`.
- Texto novo voltado ao usuário → chave no catálogo nos 3 idiomas (pt-BR, en, es).
- Uma execução pesada por vez (máquina com pouca memória).

## Mockup estático (raiz)

Front-end puro sem build (`index.html`, `js/app.js` com roteamento por `pathname` e `data-route`, `css/styles.css`); deploy estático via `vercel.json`/`_redirects`. Servir com fallback SPA (`npx serve -s .`). Regra central do produto (`docs/PLAN.md` §11): header global único da plataforma; a sidebar de cada app pertence ao workspace, nunca ao rail global.
