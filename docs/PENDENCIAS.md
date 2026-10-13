# Pendências — stack Python (atualizado em 09/10/2026)

Lista viva: atualize a cada entrega. Histórico das ondas 1–13 e da migração em [`docs/migracao/`](migracao/). As tarefas em andamento ficam na lista de tarefas
nativa do agente (`docs/migracao/TASKS.md` é o quadro da migração F0–F7, encerrado).

## Status

- **Hub Python** (`platform-py/hub`) em produção (instância do dono) desde 07/10/2026 (F16: configuração de
  produção, contas demo desativadas). Ondas 1–13: gateway, cofre, estado, login/RBAC,
  Administração, Operações (blue/green, staging, build de imagem, backups), API `/api/v1` + MCP + OAuth 2.1, Inbox,
  busca, i18n (pt-BR/en), observabilidade, uso por usuário (`/admin/usage`, coletado no gateway; `HUB_USAGE=0`
  desliga).
- **Apps oficiais** em Python: `ativos`, `fornecedores`, `servicos`, `coletor` (`platform/demo-apps-py/*`), SDK
  `hub_sdk` (`platform/sdk-python`). **Só Python** (desde 13/10/2026 sem vestígios da stack anterior);
  outras stacks: reavaliar numa expansão futura. Idiomas: pt-BR e en (`es` removido em 13/10/2026).
- **Kit do desenvolvedor** (F22): `hub-cli` (`hub new|dev|check|pack|mcp`) servida em `/dev/cli/simple/`.
- Front-end compartilhado em `platform/assets/{shell,web,i18n}` e registro de apps em `platform/config/`.

- **Distribuição 0.1.0** (tarefa #23): Apache-2.0 (`LICENSE`, `NOTICE`, `TRADEMARKS.md`), instalação do zero
  `platform-py/scripts/install.sh`, apps de exemplo opcionais (perfil `demo-apps`), nomes/porta/redes configuráveis
  (`HUB_PROJECT`, `HUB_PORT`, `HUB_SUBNET`), versão em `/healthz`, `CHANGELOG.md`, `docs/ATUALIZACAO.md`,
  `scripts/release.sh`, workflow manual de imagens no GHCR. Varredura do histórico: `docs/PUBLICACAO.md`.

## Em aberto

### Outros

- **Login MCP via Entra ID real (com o dono):** o fluxo está coberto contra o Entra falso
  (`platform-py/tests/test_oauth_entra.py`) e o guia dos clientes está em `docs/IMPLANTACAO.md` ("Conectar clientes
  de IA"). App registrado em 13/10/2026 no locatário do dono (Azure CLI; "Application Hub", segredo no volume
  `/data`, só o próprio locatário e domínio `alvesmaia.com`) e `HUB_SSO_PROVIDER=entra` ligado na instância. Falta
  o dono autenticar pelo navegador no Claude Code, no Copilot CLI e no Copilot (VS Code/app desktop).
- **Teste com agente de contexto mínimo (Haiku 5.5 `low`, kanban):** rodadas de 2026-10-09 (WebSocket/SSE) e
  2026-10-10 (repetição após as correções: os dois verdes, SSE sem JS próprio; tela conferida em duas abas).
  Correções da 2ª rodada (2026-10-10): guia "Tempo real" com módulo completo testado (SSE, WebSocket,
  `LISTEN`/`NOTIFY`), "Primeiro app" com teste à mão por `curl` (`Origin`, `Idempotency-Key`, `/_frag`), modelo
  formatado mesmo com id de 32 caracteres, mensagem clara do `openapi.json` desatualizado, índices do Contrato,
  SDK e CLI com resumo de cada página e link do texto completo. Falta, se um novo teste pedir: ajuda de servidor
  SSE/WebSocket no SDK; conformidade que espera 60 s sem explicar quando um 204 tem corpo.
- **Atualização sem parada:** em uso na instância do dono desde 13/10/2026 (`hub_update.py --build`, 0 falhas
  em 194 checagens de `/healthz` durante a troca). Pendência menor: estado em memória por réplica (métricas,
  `ux.json`, contadores do broker/WebSocket) só diverge durante os segundos da troca.
- **Repositório público:** cópia achatada publicada em 2026-10-10 (opção A de `docs/PUBLICACAO.md`); este
  repositório segue privado com o histórico completo.
- `hub check` roda só um subconjunto da conformidade; a completa é `platform-py/scripts/conformance.sh` — F22.
- Carga: `platform/loadtest/py-load.mjs` mede ~100 usuários simultâneos por instância (limite: um processo do hub; tabela em `docs/IMPLANTACAO.md`). Falta medir com vários processos/réplicas (estado em memória por réplica: métricas, analytics, uso).
- Lacunas menores: ODBC; testes reais de MySQL e SQL Server; telemetria compartilhada entre réplicas; âncora da
  auditoria por webhook; segredo do Entra ID no cofre; logout único no Entra.

## Ideias futuras

- **Aprovação humana de ação de agente no hub:** removida em 13/10/2026 (padrão MCP passou a `confirm`; a
  confirmação fica com o cliente de IA). Se voltar: pedido "Aprovar ação do agente" na Inbox com botão
  **Autorizar** + modal de confirmação (sem senha), aprovável só pela sessão do navegador (o token do agente nunca
  aprova), e o prazo do `confirm_token` renovado a partir da aprovação — no desenho antigo os 5 min contavam
  desde o pedido e a pessoa estourava o prazo até avisar o agente. Implementação anterior no histórico do git
  (`e33ae8d`).
