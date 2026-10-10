# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões: [SemVer](https://semver.org/lang/pt-BR/).
A versão do hub fica em `platform-py/hub/__init__.py` (= `platform-py/pyproject.toml`) e aparece em `GET /healthz`.
Procedimento de atualização: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md). Nova versão: `bash platform-py/scripts/release.sh <versão>`.
Versões 0.x = pré-1.0: a API pode mudar entre versões menores.

## [Unreleased]

### Adicionado
- Tempo real declarativo por SSE: `data-hub-sse` (mesmo formato de mensagem do `data-hub-ws`, polling de reserva,
  sem JavaScript de app); guia "Tempo real" com canal completo (SSE, WebSocket, `LISTEN`/`NOTIFY`).
- Documentação: índice para agentes em `/dev/llms.txt` e `/llms.txt`; índices de CLI, SDK e Contrato com resumo e
  link do texto completo; trilho ativo animado no «Nesta página»; testes à mão por `curl` no "Primeiro app".
- Estudo de atualização sem parada (`docs/ESTUDO-ATUALIZACAO-SEM-PARADA.md`).

### Corrigido
- `hub dev`: repassa WebSocket; usa o mesmo token interno do app (sem 401 em `/_hub/*`); console em UTF-8.
- Modelos da CLI: `uvicorn[standard]`; formatados mesmo com id de 32 caracteres; mensagem clara do
  `openapi.json` desatualizado. `hub check` mostra o comando de correção do lint e da formatação.
- Contrato §13 alinhado ao §25 (`connect-src` do WebSocket); teste do agendador de backup sem depender da hora.

## [0.2.0] — 2026-10-09

### Adicionado
- Kit de dashboards (BI leve): conexões SQL em Conexões — PostgreSQL, MySQL/MariaDB e SQL Server (senha no cofre,
  somente leitura por padrão, limite de linhas/tempo, teste) — e consulta dos apps pelo broker
  (`POST /_hub/sql/{conexão}/query`, auditoria sem dados).
- SDK: `hub_sdk.sql` (broker no hub, `.hub/connections.toml` no dev, `from_engine`) e `hub_sdk.dashboards`
  (datasets com cache, filtros, KPIs, gráficos SVG linha/área/barra/pizza/rosca, tabela, exportação CSV/XLSX);
  extra `hub-sdk[dashboards]`.
- CLI: `hub project create --template dashboard` (ou `--with dashboards`); `hub dev` prepara as conexões SQL locais
  com dados de exemplo. Guia `platform/docs/DASHBOARDS.md`; contrato §22.1.
- Portal de documentação em `/developers`: Começar, CLI, SDK, Guias e Contrato a partir de Markdown versionado
  (render no servidor, âncoras, copiar código, filtro, Markdown cru em `/dev/docs/*.md`); referências da CLI e do
  SDK geradas (`python -m hub.ops.devdocs`); botão «Copiar prompt» (`data-hub-copy-prompt`) por idioma.

### Alterado
- `hub`, `hub_sdk` e `hub-cli` unificados em 0.2.0 (modelos da CLI fixam `hub-sdk==0.2.0`).

## [0.1.0] — 2026-10-08

Primeira versão pública (pré-1.0). Hub em Python 3.12 (FastAPI + Jinja2 SSR, asyncpg, PostgreSQL, MinIO, Docker).

### Plataforma
- Shell global (header + menu) que nunca some; apps compostos na mesma origem, sem iframe e sem JavaScript próprio
  (comportamentos `data-hub-*`, sanitizador `hub-safe.js`, CSP por página, Trusted Types).
- Gateway `/apps/{id}/…` e `/_frag/{id}/…`, identidade assinada para os apps (`x-hub-user`), base pública
  (`x-hub-base`), WebSocket pelo gateway, prontidão obrigatória `/{app}/_hub/ready`.
- Login local com política de senha, sessões, RBAC por funções, tokens pessoais com escopo; login único com
  Microsoft Entra ID (OIDC + PKCE, mapeamento de grupos/funções).
- Administração: usuários e funções, variáveis e conexões (SharePoint/S3 com broker — credencial nunca em
  HTML/API/log), auditoria em cadeia com âncora externa assinada, cofre com chave mestra e rotação.
- Operações: implantações por pacote `.hubapp` com build de imagem, blue/green, staging, supervisor de contêineres,
  backups com credenciais de privilégio mínimo e restauração, «Provisionar armazenamento».
- Inbox (aprovações, pedidos de acesso), busca federada, Início, onboarding, analytics de uso (MAU), saúde, SLOs,
  logs e observabilidade (OTLP, perfil com OTel Collector + Jaeger).
- API pública `/api/v1` (OpenAPI 3.1, `/api/docs`) e MCP `/mcp` com OAuth 2.1 (servidor de autorização próprio),
  gerados de uma tabela única de operações.
- i18n pt-BR / en / es; identidade visual Alvesmaia (claro + escuro), PWA, recursos do aparelho
  (leitura de código de barras/QR com ZXing, fila offline, impressão).
- Kit do desenvolvedor: `hub-cli` (`hub new|dev|check|pack|mcp|deploy`) e SDK Python `hub_sdk`, servidos pelo
  próprio hub em `/dev/cli/simple/`.

### Apps de exemplo (opcionais, perfil `demo-apps`)
- `ativos` (Gestão de Ativos), `fornecedores` (Rede de Fornecedores), `servicos` (Central de Serviços),
  `coletor` (coleta por câmera/leitor).

### Distribuição
- Licença Apache-2.0 (`LICENSE`, `NOTICE`), política de marcas (`TRADEMARKS.md`).
- Instalação do zero `platform-py/scripts/install.sh` (gera segredos, compila tudo do código-fonte, cria o
  primeiro administrador); nomes de projeto/porta/redes configuráveis para várias instâncias no mesmo host.
- Workflow manual de imagens no GHCR + `docker-compose.ghcr.yml`.
