# Application Hub

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> English: [README.md](README.md) · Segurança: [SECURITY.md](SECURITY.md) · Contribuir: [CONTRIBUTING.md](CONTRIBUTING.md)
>
> **Status: experimental** — não endurecido para produção sem seguir [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md).

Portal corporativo único (white label) que hospeda várias aplicações internas: um shell global (header + menu) que nunca some, cada aplicação isolada no seu container e composta na tela sem iframe, com Administração, Operações, observabilidade, Inbox e busca global. Tudo também por **API** (`/api/v1`, OpenAPI 3.1, documentação em `/api/docs`) e por **MCP** para assistentes de IA (`/mcp`, com o mesmo controle de acesso das telas).

- **Stack:** Python 3.12 (`uv`), FastAPI + Jinja2 (SSR), asyncpg, PostgreSQL, MinIO, Docker. Hub em [`platform-py/`](platform-py/); apps Python, SDK `hub_sdk`, front-end compartilhado e suíte de conformidade em [`platform/`](platform/).
- **Branches:** `main` = stack oficial (Python); `stack/python` = branch de trabalho.

## Funcionalidades

| Área | Recursos |
|---|---|
| **Shell e experiência** | Header e menu globais fixos; Início, catálogo de aplicações, Inbox (aprovações e pedidos de acesso), busca federada (hub + apps), tour guiado, tema claro/escuro, PT-BR/EN/ES, PWA |
| **Aplicações** | Cada app no seu contêiner, alcançado só pelo gateway e composto na página sem iframe; mesma origem com CSP estrita e HTML sanitizado; tempo real por SSE e WebSocket; leitura de código de barras e câmera (`hub-device`) |
| **Acesso e segurança** | Login local ou SSO Microsoft Entra ID (com logout único); papéis e permissões por app; tokens de API pessoais com escopos; cofre cifrado para segredos; plano de controle restrito por rede |
| **Administração** | Usuários, papéis, variáveis por ambiente, conexões (Postgres, MySQL, SQL Server, SharePoint/OneDrive, S3) com broker — o app nunca vê a credencial |
| **Operações** | Implantação blue/green com prontidão obrigatória e rollback, contêineres, tarefas agendadas, backups com credencial de privilégio mínimo, atualização do hub sem parada |
| **Observabilidade** | Métricas, SLOs e página de status, uso e analytics, logs, exportação OTLP, auditoria em cadeia verificável (âncora em arquivo ou webhook) |
| **API e IA** | API pública `/api/v1` (OpenAPI 3.1, docs em `/api/docs`); servidor MCP `/mcp` com OAuth 2.1 — ferramentas do hub e dos apps com o mesmo controle de acesso das telas |
| **Para desenvolvedores** | SDK Python `hub_sdk`, CLI `hub` (`hub project create`, `hub dev`, `hub check --full` com a suíte de conformidade, `hub deploy`), modo dev com recarga, 4 apps de exemplo (Ativos, Fornecedores, Serviços, Coletor) |
| **Implantação** | Só Docker: `install.sh` gera segredos, compila tudo do código-fonte e cria o primeiro administrador; várias instâncias no mesmo host; testado em RHEL rootless |

## Roadmap

| Item | Status | Observação |
|---|---|---|
| Modo de escala pronto no compose (N cópias do hub atrás da borda em `round_robin`) | Planejado | Hoje: 1 contêiner ≈ 200 usuários simultâneos; 2 contêineres ≈ 300 (montagem manual) |
| Vários processos por contêiner (`uvicorn --workers`) | Planejado | Cada processo tenta abrir a porta interna do broker (8081) |
| ODBC no broker SQL | Ideia | Só com o driver de um banco específico (Oracle, DB2…) na imagem; Postgres, MySQL e SQL Server já são nativos |
| Aprovação humana de ação de agente na Inbox | Ideia | Hoje a confirmação de ações destrutivas fica com o cliente de IA (`confirm_token`) |
| Chaves do i18n e ids dos apps de exemplo em inglês | Ideia | Código, comentários e docs já estão em inglês; isso mexe em todo texto de tela e exige migração de dados |

Detalhes e histórico: [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md).

## Documentação

| Documento | Para quê |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Guia de trabalho: stack, comandos, guardrails |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Estado atual e como rodar |
| [`docs/PENDENCIAS.md`](docs/PENDENCIAS.md) | Status e itens em aberto |
| [`docs/FAQ-ARQUITETURA.md`](docs/FAQ-ARQUITETURA.md) | Decisões de arquitetura |
| [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md) | Checklist de implantação |
| [`platform/README.md`](platform/README.md) | Mapa de `platform/` (apps, SDK, assets, testes) |
| [`platform/docs/CONTRATO-APP.md`](platform/docs/CONTRATO-APP.md) | Contrato entre hub e aplicações |

## Instalar do zero (só Docker)

```bash
git clone https://github.com/alvesmaia/application-hub.git && cd application-hub
bash platform-py/scripts/install.sh --admin-email voce@empresa.com   # http://localhost:8090
```

Gera os segredos (`platform-py/.prod.env`, 0600), compila tudo do código-fonte (hub, apps, MinIO/mc), cria a
chave mestra do cofre e o primeiro administrador (senha temporária em `platform-py/.secrets/primeiro-acesso.txt`).
Os 4 apps de exemplo sobem por padrão (perfil compose `demo-apps`); `--no-demo-apps` instala só o hub. Outras
opções: `--public-url https://hub.empresa.com`, `--port`, `--project` (várias instâncias no mesmo host),
`--subnet`. Produção: [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md); atualização: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md);
mudanças: [`CHANGELOG.md`](CHANGELOG.md).

## Desenvolvimento (em `platform-py/`)

```bash
uv sync
uv run hub-check --all              # ruff, formato, ty, audit, pytest (+ cli, SDK, apps)
docker compose --profile demo-apps -f docker-compose.yml -f docker-compose.apps.yml up -d --build   # hub em http://127.0.0.1:8090
bash scripts/dev.sh up              # modo dev (código do disco, --reload)
```

> ⚠️ Contas de demonstração (`HUB_DEMO_USERS=1`) só em desenvolvimento; produção usa `docker-compose.prod.yml` + `prod.env.example` (ver `docs/IMPLANTACAO.md`).

## Licença / License

Código sob a [Apache License 2.0](LICENSE) — atribuições de terceiros (fontes, ZXing, ícones; MinIO é compilado à
parte, AGPL-3.0) em [`NOTICE`](NOTICE). O nome "Application Hub", a marca e o logotipo Alvesmaia **não** são
licenciados para forks: veja [`TRADEMARKS.md`](TRADEMARKS.md).

Code licensed under Apache-2.0 (third-party notices in `NOTICE`); the "Application Hub" name and Alvesmaia marks/logo
are not licensed for forks' branding — see `TRADEMARKS.md`.
