# Tarefas

## Docs
- ✅ 29 · Importação manual mais clara + passo a passo nas docs, testado de ponta a ponta
  - ✅ aba Importar: arquivo → prévia → variáveis/segredos → ambiente → implantar; validador de manifest na aba Pipeline e CI, vazio
  - ✅ prévia do pacote (POST /api/ops/packages/preview): id, nome, versão, novo/atualização, requires (sem implantar)
  - ✅ campos para requires.variables/secrets (vazio = manter; segredo sem eco, nunca devolvido); config também como arquivo
  - ✅ botão diz o efeito: "Implantar em staging" / "Enviar para aprovação (produção)"
  - ✅ guia Publicar + AGENTS, comecar, CONTRATO-APP, FAQ; E2E 31-import-preview e 06-ops; ui-sweep 0 achados

## MCP
- ✅ 30 · Publicar pacote pelo MCP: deployments_upload devolve link de envio de uso único (curl -F package=@…)
  - ✅ /mcp/uploads/<ticket>: 10 min, uso único atômico, dono e permissão conferidos no envio; passa pelo Access
  - ✅ corpo montado sem cópia por pedaço (pacote de 20 MB)
- ✅ 31 · Inventário da API: ~75 ferramentas novas no MCP (destrutivas com confirm_token)
  - ✅ fora de propósito: criar token, trocar senha, chave do cofre, aprovar JavaScript, estado de tela, SSE/CSV
- ✅ 32 · Guia do agente: publicar e operar pelo MCP (mcp-api.md, AGENTS.md); teste exige toda ferramenta no guia

## Fechamento
- ▶️ 33 · hub-check, E2E e ui-sweep na instância isolada, deploy na :8090, limpeza
  - ✅ hub-check --all verde (30/30) na main combinada
  - ⬜ E2E das telas de Implantações + ui-sweep --quick na instância isolada
  - ⬜ push, deploy sem parada na :8090, limpeza das worktrees
