# F22 — Kit do desenvolvedor

Máquina do desenvolvedor: **só uv/Python** (sem Docker, sem Postgres, sem acesso ao repositório).

## Entrega 1 — CLI, template, SDK e `hub dev`/`hub check` sem Docker ✅

- **`hub project create [ID]`** (`platform-py/cli/hub_cli/project.py`): interativo com TTY e parâmetros faltando;
  não interativo com `--yes` ou sem TTY (`--name --stack --db --requires --dir --json`). Parâmetro
  ausente/inválido → mensagem + **código 2**. Códigos estáveis (`hub_cli/errors.py`, no `--help`):
  0 ok · 1 falha · 2 validação · 3 autenticação · 4 hub inacessível. **`hub project stacks [--json]`**: stacks
  embutidas (hoje `python-fastapi`).
- **Template `python-fastapi`** (`hub_cli/templates/python-fastapi`): FastAPI + `hub_sdk` + Jinja2 (workspace do SDK,
  classes do hub, sem JS), `/_hub/ready` (HubSdk), base por `x-hub-base`, manifesto com **`requires`**, API v1 com
  `operation_id` + `openapi.json` gerado, Dockerfile (não-root, HEALTHCHECK), pyproject com ruff(+S)/ty/pytest+cov≥80,
  páginas Início/Itens + rota de API + testes, `.env.example`, `AGENTS.md` do app. O SDK vai em `vendor/` (fonte uv).
- **`requires` no hub**: `hub.models.AppRequires` + `requires_error` (pipeline `_manifest`, validador de manifesto);
  contrato §14.
- **SDK**: `hub_sdk.db.database_url/engine/upgrade` (SQLAlchemy Core + Alembic; SQLite no dev, Postgres no hub;
  extra `hub-sdk[sql]`); `hub_sdk.storage.LocalConnection` (`HUB_STORAGE_LOCAL_DIR`, mesma API).
- **`hub dev`** sem `--upstream`: hub em processo (`hub.devapp`, embutido no wheel com os assets do shell) +
  `uv run uvicorn … --reload`; SQLite `.hub/dev.db`, armazenamento `.hub/storage/`, tarefas em processo.
  Filhos em grupo próprio, encerrados com a árvore (Windows: `taskkill /T`).
- **`hub check`**: manifesto, ruff, formato, ty, openapi (regenera), pytest+cov, conformidade local
  (§2/§3/§3.0/§12/§13/§23 contra o app de pé atrás do hub em processo). `--only`, `--json`.
- **Wheel**: `bash platform-py/scripts/build-cli.sh` → `platform-py/dist/cli/hub_cli-0.2.0-py3-none-any.whl`
  (+ `hub_sdk-1.0.0`). Pacote `hub` e assets via `force-include`; `_pack` nunca leva `.env`, `.hub/`, `*.hubapp`.

**Prova** (fora do repositório, `%TEMP%/devkit-proof`): `uv tool install --force hub_cli-0.2.0-py3-none-any.whl`;
`hub project create estoque --stack python-fastapi --yes --json` → `uv sync` → `hub check` **verde**
(manifesto, ruff, formato, ty, openapi, pytest, conformidade) → `hub dev --port 4187`:
`GET /apps/estoque` 200 com `platform-header`; POST do formulário pelo `/_frag` 200; captura
`platform-py/dist/shots/devkit-dev.png` (shell + workspace Itens com o item criado). Script da captura:
`platform-py/scripts/devkit-shot.mjs` (`DEVKIT_E2E_DIR` = pasta com `@playwright/test`).
Template usa as classes padrão dos fragmentos (`demo.css` servido pelo app, embutido no wheel; `hub-panel`,
`hub-form-grid`, `hub-actions`): links da sidebar sem sublinhado, botão alinhado, painel com respiro.
**ui-sweep `--quick` contra o `hub dev`: 0 achados** (3 rotas × claro/escuro;
`loadtest/results/2026-10-08/ui-sweep-131928-quick/`). Modo novo da varredura: `SWEEP_DEV_HUB=1`
(sem login), `SWEEP_ROUTES=/apps/estoque,/apps/estoque/itens`, `SWEEP_PREFIX=/apps/estoque`
(no Git Bash: `MSYS_NO_PATHCONV=1`).

Nota: em worktree dentro de `.claude/` (pasta ignorada) o `ty` do `hub-check --all` não acha o ambiente
efêmero do `uv run --with` para SDK/apps; com `.venv` local em cada projeto passa (como no checkout normal).

## Entrega 2 — `hub deploy`/`hub login` e provisionamento por `requires` ✅

- **CLI** (`hub_cli/deploy.py`): `hub login --hub URL [--token-file F]` (token pedido sem eco; salvo em
  `%APPDATA%`/`~/.config` `hub-cli/credentials.json`, 0600 fora do Windows); `hub deploy --env staging|production
  [--hub --token --env-file --json]`: valida o manifesto, regenera `openapi.json`, empacota (`build/**`, nunca
  `.env*`, `.hub/`, `*.hubapp`) e envia a `/api/ops/packages?wait=true` com `config` = valores de
  `requires.variables/secrets` do `deploy.<env>.env` (ou perguntados, segredo sem eco). Valores nunca saem na
  tela/JSON (só os nomes). Códigos: 2 validação, 3 token recusado, 4 hub inacessível, 1 pacote rejeitado.
- **Hub** (`hub/ops/newapp.py`): app novo entra no registro na 1ª implantação aprovada pelo pipeline
  (`<dados>/apps.dynamic.json`, recarregado na subida; listas de apps da Administração/Conexões); container
  inicial por molde endurecido (raiz só leitura, `cap_drop ALL`, `no-new-privileges`, `/scratch` tmpfs) na rede
  `HUB_APP_NETWORK`; `requires.database` → `provision.provision` (banco/papel por app × ambiente, `HUB_DB_URL`),
  `requires.storage` → «Provisionar armazenamento» (gancho das Conexões), variáveis/segredos → variáveis geridas
  `NOME_<APP>[_STAGING]` injetadas como `NOME` (segredo `secreta`, mascarado). O upload manual de um `.hubapp`
  (`hub pack`) passa pelo mesmo `run_import` (mesmo provisionamento, sem valores). Smoke test pulado para app
  novo sem container. Token de CI (`upload`/`status`, sem dono) autoriza só essas ações (antes: 403).
- **Prova** (`bash platform-py/scripts/devkit-instance.sh up|down`: hub temporário `app-hub-devkit` em :8094, cópia
  do `/data`, banco `app-hub-devkit-db`, rede e prefixo `app-hub-devkit-app-*` próprios — nada de produção tocado;
  removido ao fim): `hub login --token-file` → `hub deploy --env staging --json` → 201 «implantada (blue/green)»,
  `provisioned = {database: app_estoque_staging, variables: [SAUDACAO], secrets: [API_KEY]}`;
  `/hub-env?to=staging&next=/apps/estoque` → 200 no shell, `/_frag/estoque/itens` 200; container
  `app-hub-devkit-app-estoque__staging` healthy com `HUB_DB_URL`, `SAUDACAO`, `API_KEY`; o valor do segredo não
  aparece em `/api/admin/env`, `/api/ops/deployments` nem nos logs do hub. `hub deploy --env production` →
  «Pacote validado — aguardando aprovação para produção» (banco `app_estoque` provisionado).
- Template: Dockerfile instala o uv por `pip` (o pull de `ghcr.io` falhava na rede da máquina).

## Entrega 3 — distribuição pelo hub e tela Desenvolvedores ✅

- **Distribuição** (`hub/ops/devkit.py`): a imagem do hub constrói os wheels (`Dockerfile`, estágio `cli` →
  `/platform/dist/cli`) e serve um índice PEP 503 **público e só leitura** em `/dev/cli/simple/` (lista fechada:
  `hub_cli-*`/`hub_sdk-*`, com `#sha256`) + `/dev/cli/files/<wheel>`. Instalação:
  `uv tool install hub-cli --index <hub>/dev/cli/simple/` (ou a URL direta do wheel). `HUB_CLI_DIST` muda a pasta.
- **Tela «Desenvolvedores»** (`/developers`, grupo Aplicações do menu e paleta; permissão «Acessar aplicações»):
  comando de instalação com o endereço público do hub, arquivo direto e versão, aviso de token (botão «Criar
  token» → Credenciais), comandos, stacks, links para o `AGENTS.md` do app e o contrato (`/dev/docs/*.md`, com
  sessão) e para o validador de manifesto (Implantações). Textos nos 3 idiomas (`catalog.json`).
- **Prova** (hub temporário `app-hub-devkit`, removido ao fim): `uv tool install hub-cli --index
  http://127.0.0.1:8094/dev/cli/simple/` fora do repositório instalou a CLI 0.2.0; ui-sweep em `/developers`:
  `--quick` 0 achados e matriz completa (admin/operador/leitor × claro/escuro/SO-escuro @1440 + 2 @1280) 0 achados;
  capturas `platform-py/dist/shots/devkit-developers{,-en}.png`.
- **Produção** (2026-10-08, `application-hub-py` em `0a2c7dd`, compose `docker-compose.yml + apps + prod` com
  `.prod.env`): `build -q app-hub-web && up -d --no-deps app-hub-web`; `/healthz` 200, `/dev/cli/simple/` 200
  (hub-cli, hub-sdk), `/developers` exige sessão (303 sem login).

## Entrega 4 (tarefa #24) — portal de documentação em `/developers` ✅

- **Seções** (sub-navegação à esquerda + «Nesta página»): Começar (`/developers`), CLI, SDK, Guias
  (`/developers/guias/{primeiro-app,publicar,banco,armazenamento,telas,mcp-api,dashboards}`), Contrato.
- **Conteúdo** = Markdown versionado em `platform/docs/dev/` (+ `platform/docs/CONTRATO-APP.md`), renderizado no
  servidor por `hub/ops/devdocs.py` (markdown-it-py, HTML do Markdown desligado; h1 do .md vira h2; âncoras;
  blocos de código com «Copiar»; tabelas roláveis; `@HUB@` = endereço público). Markdown cru para agentes em
  `/dev/docs/<nome>.md` (`comecar`, `cli`, `sdk`, `guia-<slug>`, `contrato`, `agents`). Corpo em pt-BR (aviso em
  en/es); interface nos 3 idiomas.
- **Referências geradas**: `cli.md` (argparse da CLI: comandos, parâmetros, padrões, exemplos, códigos de saída) e
  `sdk.md` (assinaturas/docstrings do `hub_sdk` por AST) — `uv run python -m hub.ops.devdocs`; o teste
  `test_generated_references_are_current` reprova se esquecer (vale também para `hub_sdk.dashboards` da #21).
- **«Copiar prompt»** (Começar): componente `data-hub-copy-prompt` em `platform/assets/web/devdocs.js` (+
  `devdocs.css`, só tokens): barra de agentes (marcas SVG neutras desenhadas no hub, nome no `title`/`aria-label`),
  prompt editável (EDITÁVEL) por idioma, rodapé, `aria-live`; copiado = verde com ✓ por 2,2 s; fora de contexto
  seguro cai para `execCommand`.
- **Prova** (instância isolada `iso-instance.sh`): ui-sweep matriz completa em `/developers/*` (3 papéis × claro/
  escuro/SO-escuro/auto @1440 + @1280 + 390) **0 achados** (jargão técnico da documentação na allowlist, só nessas
  rotas); E2E `tests/30-developers-docs.spec.ts` (navegação, âncoras, filtro, Markdown cru, cópia com área de
  transferência simulada, fallback) verde; capturas `platform-py/dist/shots/docs-{comecar,cli,guia-telas}-{light,dark}.png`.

## Lacunas

- `hub.example.com` está atrás do Cloudflare Access: de fora da rede, o `uv tool install --index` precisa de
  um token de serviço do Access (ou liberar só `/dev/cli/*` na política do Access) — decisão do dono.
- Token de CI sem dono cria implantações como `token:<nome>`; aprovação de produção segue na tela Implantações.
- `hub check` não roda a suíte de conformidade completa (subconjunto §2/§3/§12/§13/§23); a completa segue em
  `scripts/conformance.sh`.
- Em worktree sob `.claude/`, o `ty` dos apps precisa de `.venv` local (ver nota da entrega 1).
