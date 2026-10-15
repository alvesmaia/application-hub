# Melhorias de UX/Design

## Paginação
- ✅ 18 · Paginação (padrão de /admin/audit) em /admin/users, /admin/runtime, /admin/backups
  - ✅ /admin/users: já paginado (pager aparece com > 10 linhas); aba Funções são cartões editáveis, sem paginação
  - ✅ /admin/runtime: tabela única de contêineres paginada
  - ✅ /admin/backups: histórico paginado (políticas = 1 linha por app)

## Menu
- ✅ 19 · Desenvolvedores sai da barra lateral e vai para o menu do usuário
  - ✅ menu do usuário (desktop e celular), mesma permissão (editar_configuracoes)
  - ✅ abre em nova guia (target=_blank, aviso para leitor de tela)
  - ✅ /developers* sempre sem a barra lateral; logado vê "Voltar ao início"

## Configuração das apps
- ✅ 20 · /admin/env: remover a seção "por aplicação"; tabela de variáveis ganha coluna Aplicação com filtro dropdown de múltipla seleção
  - ✅ coluna Aplicação + filtro ?app= (checkboxes, GET); sem vínculo só aparece sem filtro
  - ✅ botões de banco/token (provisionar, staging, rotacionar) foram para Containers › detalhes do app; mensagens apontam para lá

## Inbox
- ✅ 22 · Filtros em dropdown e paginação
  - ✅ botões de filtro viram um select "Mostrar" (mesmos ?filter=, autosubmit)
  - ✅ lista paginada (pager mantém o filtro; limite de 200 removido)

## Header
- ✅ 23 · Ícone do GitHub no header (HUB_SOURCE_URL; padrão = repositório oficial; vazio esconde)

## Docs
- ✅ 24 · Prompt de copiar aponta para /dev/docs/AGENTS.md (regras do app em AGENTS-app.md; nomes antigos → 308)

## MCP / OAuth
- ✅ 25 · Avisos "Duplicate Operation ID" nos apps desde o openapi_of da tarefa 10
- ⛔ 26 · Login OAuth do MCP falha externamente — servidor OK em todas as etapas; falta o cliente e o erro visto
  - ✅ público: /mcp 401 + resource_metadata; metadata 200; /oauth/register, token, revoke passam pelo Access
  - ✅ registro dinâmico com retorno https (claude.ai) aceito; /oauth/authorize → Access → /login com a URL inteira
  - ✅ login Entra guarda a URL de volta (cookie Secure/HttpOnly/Lax, 10 min); app do Entra do login intacto
  - ⬜ dono remove os clientes diag-claude-ai e diag-copilot-cli em Credenciais › OAuth

## Fechamento
- ✅ 21 · Integração, E2E das telas, ui-sweep, deploy na :8090, limpeza
  - ✅ hub-check --all verde; E2E completo na instância isolada (141 + specs corrigidos 16/16)
  - ✅ ui-sweep --quick: 0 achados (docs logadas voltaram a seguir o tema da conta)
  - ✅ hub_update sem parada; 4 apps reconstruídos (pino keep-20261015b), ready 200, sem avisos de operation id
