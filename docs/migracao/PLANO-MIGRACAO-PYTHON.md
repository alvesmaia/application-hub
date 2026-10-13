# Plano de migração do Application Hub para Python

Branch `stack/python`, worktree `C:\PROJETOS\PROD\application-hub-py`. O hub em Rust (`stack/rust-topcoat`) segue no ar em `hub.example.com` até o dono validar a versão nova, publicada em `hub-dev.example.com` durante o desenvolvimento.

## Decisões fixas

- **Stack do hub:** Python 3.12, FastAPI + Jinja2 (SSR), asyncpg (SQL direto, sem ORM pesado), Alembic para migrações, httpx (proxy/clientes), `websockets`/Starlette WS, `docker` SDK, structlog, OpenTelemetry.
- **Qualidade:** `ruff` (lint **e** formatação, mesmas regras do SDK Python, incluindo Bandit `S`), Astral `ty` (regras de tipos em erro), `pytest` + `pytest-asyncio` + `pytest-cov` (mínimo 80% em auth, vault, state, admin e gateway), `uv audit`; gerenciado por `uv`.
- **Modelos e configuração:** Pydantic v2 para validação/modelos; `pydantic-settings` para `hub.toml` e variáveis `HUB_*`.
- **Apps:** só Python oficial (opção A). SDK Rust descontinuado; Ativos, Fornecedores e Serviços reescritos em Python. O contrato HTTP não muda (outras linguagens rodam se passarem na conformidade, sem suporte).
- **Front-end:** inalterado. CSS, JS (`hub*.js`), fontes, ícones e `catalog.json` são copiados como estão; os templates Jinja2 geram **o mesmo HTML** do Topcoat.
- **Features novas durante a migração:** direto em Python, nas áreas já migradas.
- **Prazo:** 3 a 6 meses.

## Garantia de paridade (cada página só vira "pronta" depois de)

1. **Paridade de HTML:** a mesma rota renderizada pelo hub Rust e pelo Python, com os mesmos dados, comparada estruturalmente (`tests-e2e/parity`).
2. **Regressão visual:** as 24 referências batem.
3. **Testes de ponta a ponta e ui-sweep:** a suíte atual (Playwright, ui-sweep, conformidade) roda contra o hub Python sem mudanças.
4. **API/MCP:** o `openapi.json` gerado é equivalente ao atual (`docs/openapi/hub-v1.json`); o cliente MCP independente passa.

## Etapas

| Etapa | Conteúdo | Saída verificável |
|---|---|---|
| **F0 — Fundação** | Pacote `hub/` em `platform-py/`, uv/ruff/mypy/pytest, app FastAPI vazio servindo os assets atuais, compose próprio na porta 8090 com banco separado, túnel + Access para `hub-dev.example.com`, `ci-local` com etapa Python do hub | `hub-dev.example.com` abre a casca do hub com o visual atual |
| **F1 — Núcleo** | Configuração (`hub.toml`), estado em Postgres (equivalente ao `hub-state`), autenticação/sessões/RBAC/tokens (`hub-auth`), cofre (`hub-vault`, mesmo formato de chave), auditoria em cadeia (`hub-admin/chain`, formato compatível), gateway com identidade assinada `x-hub-user`, `x-hub-base`, CSP, WebSocket, redirecionamentos `/apps/` | Login, shell, gateway para os apps atuais; testes de auth e gateway |
| **F2 — Páginas do usuário** | Início, Aplicações, Status, busca ⌘K, Inbox, Configurações, tokens, apps autorizados, telas de credencial, i18n | Paridade + E2E dessas rotas |
| **F3 — Administração/Operações** | Usuários, funções, variáveis, conexões, implantações, containers, tarefas, backups, logs, auditoria, flags, analytics, observabilidade, saúde | Paridade + E2E + ui-sweep completo |
| **F4 — API e MCP** | API `/api/v1` gerada da tabela de operações (FastAPI/OpenAPI), MCP (Streamable HTTP, duas eras) + OAuth 2.1, CORS máquina-a-máquina | OpenAPI equivalente, `oauth_mcp`/`api_mcp` portados, cliente MCP independente |
| **F5 — Operações pesadas** | Blue/green e staging (Docker), outbox e tarefas duráveis, backups/DR, multi-réplica com liderança, telemetria, modo dev | DR, zero downtime, caos e réplicas passam |
| **F6 — Apps em Python** | Ativos, Fornecedores e Serviços em Python; remoção do SDK Rust; conformidade dos 5 apps | Conformidade e paridade dos apps |
| **F7 — Virada** | `ci-local` só Python, validação pesada, documentação; com o "ok" do dono, `hub.example.com` passa a servir o hub Python; Rust arquivado | Validação do dono |

## Forma de trabalho

- **Uma etapa por vez**, dividida em tarefas pequenas (um módulo ou um grupo de páginas), cada uma com teste de paridade.
- **Delegação:**
  - **Codex CLI** (`gpt-6-luna`, esforço `medium`, MCP `ai-memory`, ponytail + caveman): portes bem especificados de módulos Rust → Python com testes (ex.: `hub-vault`, `hub-auth`, `hub-state`).
  - **Subagentes Claude** `sonnet` (esforço médio) para integração, telas e paridade; `haiku` para tarefas mecânicas (templates repetitivos, traduções, ajustes de teste).
  - Todos com **ponytail** (código mínimo, sem sobre-engenharia) e **caveman** (relatórios curtos).
- **Acompanhamento:** cada tarefa grava status em `docs/migracao/status/<tarefa>.md`; verificação periódica; nada é dado como pronto sem o teste de paridade da tarefa.
- **Validação:** leve durante o dia (testes da tarefa + ui-sweep rápido no hub novo); pesada no fim do dia.
- **Auditoria pesada Python:** percorrer todos os projetos Python; executar `uv audit` onde há `uv.lock` (`platform-py`, SDK e apps com esse formato). Projetos com outro formato de lock precisam gerar `uv.lock` antes de usar este comando.
- **Recursos da máquina:** os dois hubs ficam no ar; o novo roda em modo dev leve (uvicorn) até a F5. Nada pesado em paralelo com a validação do hub atual.

## Tamanho de referência (Rust a portar)

`apps/hub-web` ≈ 39,8 mil linhas; `hub-ops` 18,6 mil; `hub-admin` 8,4 mil; `hub-auth` 5,4 mil; `hub-runtime` 3,9 mil; `hub-storage` 3,9 mil; `hub-gateway` 2,9 mil; `hub-state` 2,8 mil; `hub-shell` 2,8 mil; `hub-cli` 2,1 mil; `hub-vault` 1,7 mil; `hub-graphsim` 1,4 mil; `hub-telemetry` 1,3 mil; `hub-manifest` 1,0 mil; `hub-flags` 0,5 mil; `hub-registry` 0,5 mil. SDK Rust (8,9 mil) é descontinuado.
