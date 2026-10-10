# Pendências — stack Python (atualizado em 09/10/2026)

Lista viva: atualize a cada entrega. O histórico completo da era Rust (ondas 1–13) está em
[`docs/migracao/historico-rust/PENDENCIAS.md`](migracao/historico-rust/PENDENCIAS.md); o que cada etapa da
migração entregou, em [`docs/migracao/status/`](migracao/status/). As tarefas em andamento ficam na lista de tarefas
nativa do agente (o `TASKS.md` da raiz é o quadro da migração F0–F7, encerrado).

## Status

- **Hub Python** (`platform-py/hub`) em produção (instância do dono) desde 07/10/2026 (F16: configuração de
  produção, contas demo desativadas). Paridade funcional com o hub Rust (ondas 1–13): gateway, cofre, estado, login/RBAC,
  Administração, Operações (blue/green, staging, build de imagem, backups), API `/api/v1` + MCP + OAuth 2.1, Inbox,
  busca, i18n (pt-BR/en/es), observabilidade.
- **Apps oficiais** em Python: `ativos`, `fornecedores`, `servicos`, `coletor` (`platform/demo-apps-py/*`), SDK
  `hub_sdk` (`platform/sdk-python`); SDK Rust descontinuado.
- **Kit do desenvolvedor** (F22): `hub-cli` (`hub new|dev|check|pack|mcp`) servida em `/dev/cli/simple/`.
- **Código Rust removido da `main`** (tarefa #20); arquivado na branch `stack/rust-topcoat`. Front-end compartilhado
  agora em `platform/assets/{shell,web,i18n}` e registro de apps em `platform/config/`.

- **Distribuição 0.1.0** (tarefa #23): Apache-2.0 (`LICENSE`, `NOTICE`, `TRADEMARKS.md`), instalação do zero
  `platform-py/scripts/install.sh`, apps de exemplo opcionais (perfil `demo-apps`), nomes/porta/redes configuráveis
  (`HUB_PROJECT`, `HUB_PORT`, `HUB_SUBNET`), versão em `/healthz`, `CHANGELOG.md`, `docs/ATUALIZACAO.md`,
  `scripts/release.sh`, workflow manual de imagens no GHCR.

## Em aberto

- **Teste com agente de contexto mínimo (Haiku 5.5 `low`, kanban):** rodadas de 2026-10-09 (WebSocket/SSE) e
  2026-10-10 (repetição após as correções: os dois verdes, SSE sem JS próprio; tela conferida em duas abas).
  Correções da 2ª rodada (2026-10-10): guia "Tempo real" com módulo completo testado (SSE, WebSocket,
  `LISTEN`/`NOTIFY`), "Primeiro app" com teste à mão por `curl` (`Origin`, `Idempotency-Key`, `/_frag`), modelo
  formatado mesmo com id de 32 caracteres, mensagem clara do `openapi.json` desatualizado, índices do Contrato,
  SDK e CLI com resumo de cada página e link do texto completo. Falta, se um novo teste pedir: ajuda de servidor
  SSE/WebSocket no SDK; conformidade que espera 60 s sem explicar quando um 204 tem corpo.
- **Atualizações sem interrupção e com reversão automática:** estudo em `docs/ESTUDO-ATUALIZACAO-SEM-PARADA.md`
  (2026-10-10). Recomendação: azul/verde atrás de um proxy (Caddy), comando `hub-update` com backup, testes,
  troca sem reinício e volta automática, migrações só aditivas e aviso opcional de versão nova. Esforço:
  ~6 dias. Aprovado pelo dono em 2026-10-10: em implementação.
- Cloudflare Access da instância do dono: `/developers*`, `/dev/*` (CLI, `.md`, `/dev/llms.txt`) e `/assets/*` já
  são públicos; `/llms.txt` na raiz ainda pede login (a conexão do Cloudflare usada aqui não tem escrita no Access:
  incluir o caminho no app "MCP/OAuth máquina-a-máquina" pelo painel).
- `hub check` roda só um subconjunto da conformidade; a completa é `platform-py/scripts/conformance.sh` — F22.
- Sem matriz de carga em Python (DR, caos e zero downtime têm `platform/loadtest/py-dr.mjs` e `py-resilience.mjs`).
- Paridade de HTML/pixels (`platform-py/parity`) compara com um hub Rust no ar — só funciona subindo a branch arquivada.
- Lacunas menores: ODBC; testes reais de MySQL e SQL Server; telemetria compartilhada entre réplicas; âncora da
  auditoria por webhook; segredo do Entra ID no cofre; logout único no Entra.
