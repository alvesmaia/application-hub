# Application Hub

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> English: [README.en.md](README.en.md) · Segurança: [SECURITY.md](SECURITY.md) · Contribuir: [CONTRIBUTING.md](CONTRIBUTING.md)
>
> **Status: experimental** — não endurecido para produção sem seguir [`docs/IMPLANTACAO.md`](docs/IMPLANTACAO.md).

Portal corporativo único (white label) que hospeda várias aplicações internas: um shell global (header + menu) que nunca some, cada aplicação isolada no seu container e composta na tela sem iframe, com Administração, Operações, observabilidade, Inbox e busca global. Tudo também por **API** (`/api/v1`, OpenAPI 3.1, documentação em `/api/docs`) e por **MCP** para assistentes de IA (`/mcp`, com o mesmo controle de acesso das telas).

- **Stack:** Python 3.12 (`uv`), FastAPI + Jinja2 (SSR), asyncpg, PostgreSQL, MinIO, Docker. Hub em [`platform-py/`](platform-py/); apps Python, SDK `hub_sdk`, front-end compartilhado e suíte de conformidade em [`platform/`](platform/).
- **Branches:** `main` = stack oficial (Python); `stack/python` = branch de trabalho.

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
| [`docs/migracao/`](docs/migracao/) | Histórico da migração para Python (status por etapa; documentos antigos em `historico-rust/`) |

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
