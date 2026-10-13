# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões: [SemVer](https://semver.org/lang/pt-BR/).
A versão do hub fica em `platform-py/hub/__init__.py` (= `platform-py/pyproject.toml`) e aparece em `GET /healthz`.
Procedimento de atualização: [`docs/ATUALIZACAO.md`](docs/ATUALIZACAO.md). Nova versão: `bash platform-py/scripts/release.sh <versão>`.
Versões 0.x = pré-1.0: a API pode mudar entre versões menores.

## [Unreleased]

### Removido
- Idioma espanhol (`es`): o hub e o SDK ficam em pt-BR e inglês. Quem tinha `es` salvo ou navegador em espanhol
  passa a ver inglês; `PUT /api/me/prefs` com `lang: "es"` responde 400.
- Vestígios da stack anterior: ferramenta de paridade (`platform-py/parity`), cópias dos apps antigos em
  `platform/demo-apps` (fica só `assets/demo.css` e o jsdom dos testes JS), exemplos de SDK que não é Python no
  Contrato e nos guias; E2E e `hub pack` dos testes passam a usar a pilha `app-hub` e a CLI Python. Só Python.
- Aprovação de ação de agente na Inbox (`destructive_actions = "approve_in_hub"`, página
  `/account/agent-approval/{id}`): o padrão passa a `confirm` — a 1ª chamada MCP destrutiva devolve um
  `confirm_token` e a 2ª executa; quem confirma com a pessoa é o cliente de IA. `approve_in_hub` em configuração
  agora é recusado na partida (use `confirm` ou `deny`). Pedidos de agente antigos somem da Inbox.

### Adicionado
- Uso por usuário (Configurações › Uso por usuário, `/admin/usage`): aberturas, navegações, ações, dias ativos e
  último acesso por usuário e por app, com filtro de período/usuário/app e CSV. Coletado no gateway, sem exigir nada
  dos apps; ligado por padrão (`HUB_USAGE=0` desliga), mesma retenção do analytics. API `GET /api/v1/usage`
  (`usage_query`, também no MCP); acesso com "Editar configurações" ou "Ver auditoria".
- `install.sh`: modo interativo (sem opções, num terminal) — pergunta admin, porta, bind, URL pública, projeto,
  faixa de rede e apps de exemplo com o padrão entre colchetes, mostra um resumo e pede confirmação; `--yes` mantém
  o modo não interativo do CI. `--get-compose` baixa o docker-compose oficial (versão fixa, sha256 conferido) em
  `~/.local/bin`, para RHEL/Rocky sem root.
- Tempo real declarativo por SSE: `data-hub-sse` (mesmo formato de mensagem do `data-hub-ws`, polling de reserva,
  sem JavaScript de app); guia "Tempo real" com canal completo (SSE, WebSocket, `LISTEN`/`NOTIFY`).
- Documentação: índice para agentes em `/dev/llms.txt` e `/llms.txt`; índices de CLI, SDK e Contrato com resumo e
  link do texto completo; trilho ativo animado no «Nesta página»; testes à mão por `curl` no "Primeiro app".
- Atualização sem parada: borda `app-hub-edge` (Caddy) na `HUB_PORT`, `scripts/hub_update.py` (backup, cópia
  com a versão nova, troca, observação e volta automática; `--rollback`), `/readyz` que confere banco e dados,
  aviso opcional de versão nova em Saúde (`HUB_UPDATE_CHECK=1`). Testado sob carga: 0 pedidos perdidos.

### Atenção
- A `HUB_PORT` passa do `app-hub-web` para a borda `app-hub-edge`: o primeiro `up -d` desta versão tem uma
  parada de ~10 s. Proxies próprios na frente continuam iguais (o destino é a mesma porta).

### Corrigido
- `hub_update.py`: no Docker com containerd, o `--build` falhava no backup porque a imagem em execução perdia a
  referência ao ganhar `latest` nova; agora ela recebe a tag `<proj>-web:pre-update` antes (backup, rollback e volta
  usam a tag). O backup do volume de dados não reprova mais quando um arquivo muda durante a cópia.
- Tour guiado do Início: Esc fecha com o foco em qualquer lugar da tela (antes só dentro do tour) e há botão
  Fechar (×); os dois guardam o passo para retomar depois.
- MCP 2026-07-28: listas trazem `ttlMs` e `cacheScope` (o Claude Code recusava o `tools/list`); o SDK não perde
  mais os tipos de esquemas fundos ao montar ferramentas a partir do OpenAPI do app.
- Instalar sem os apps de exemplo (`HUB_APPS_JSON=./apps.empty.json`) derrubava o hub no boot
  (`Aplicação desconhecida: ativos`): a carga inicial de variáveis descarta vínculo de app fora do catálogo.
- `hub deploy`: a recusa do hub volta com o motivo (a razão vem no campo `error`) — antes um pacote grande dizia só
  `400`. `hub pack` recusa antes de enviar acima de 20 MB e aponta os maiores arquivos; o hub diz o tamanho e o
  limite. Limite documentado no guia de publicação.
- `hub check`: a falha de conformidade `§3 página`/`§3 fragmento` traz o status e a última exceção de
  `.hub/check-app.log` (e, em `no such column`/`no such table`, a dica de apagar `.hub/*.db`).
- Podman rootless (RHEL/Rocky): apps sobem — tmpfs `/scratch` com `U` em vez de `uid=`/`gid=` (recusados pelo
  Podman); sem o controlador `cpu` delegado ao usuário, build e contêineres saem sem limite de CPU em vez de todo `RUN`
  falhar; socket do compose configurável (`HUB_CONTAINER_SOCKET_HOST`) e `install.sh` que detecta o Podman rootless,
  avisa sobre linger/CPU e grava o socket; `HUB_BIND6` evita o conflito do `[::1]` com `--bind 0.0.0.0`;
  `install.sh --clone` usa o repositório público. Guia em docs/IMPLANTACAO.md (Podman rootless).
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
