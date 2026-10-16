# Tarefas

## Docs
- ▶️ 29 · Importação manual mais clara + passo a passo nas docs, testado de ponta a ponta
  - ⬜ aba Importar: arquivo → ambiente → implantar; validador de manifest sai do caminho (aba Pipeline e CI, sem exemplo preenchido)
  - ⬜ prévia do pacote ao escolher o arquivo: id, nome, versão, novo/atualização, requires (sem implantar)
  - ⬜ campos para requires.variables/secrets (vazio = manter; segredo sem eco, nunca devolvido)
  - ⬜ botão diz o efeito: "Implantar em staging" / "Enviar para aprovação (produção)"
  - ⬜ guia Publicar: passo a passo da importação manual; E2E + ui-sweep

## MCP
- ⬜ 30 · Publicar pacote pelo MCP: deployments_upload devolve link de envio de uso único (curl -F package=@…)
- ⬜ 31 · Inventário da API: expor no MCP toda ação de escrita que falta (permissões e confirmação das destrutivas)
- ⬜ 32 · Guia do agente: publicar e operar pelo MCP (AGENTS.md e guia API e MCP)

## Fechamento
- ⬜ 33 · hub-check, E2E e ui-sweep na instância isolada, deploy na :8090, limpeza
