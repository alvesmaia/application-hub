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
- ✅ 26 · Login OAuth do MCP falha externamente — cliente do registro dinâmico expirava em 1 h
  - ✅ público: /mcp 401 + resource_metadata; metadata 200; /oauth/register, token, revoke passam pelo Access
  - ✅ registro dinâmico com retorno https (claude.ai) aceito; /oauth/authorize → Access → /login com a URL inteira
  - ✅ login Entra guarda a URL de volta (cookie Secure/HttpOnly/Lax, 10 min); app do Entra do login intacto
  - ✅ reproduzido com mcp-remote em container pela internet: client_id em cache → "client_id desconhecido"
  - ✅ correção: registro não usado vale 30 dias (teste test_registered_client_survives_a_late_sign_in)
  - ✅ deploy na :8090; container pela internet: login, token, initialize, tools/list, status_get ok
  - ⬜ dono remove os clientes de teste diag-claude-ai, diag-copilot-cli e "MCP CLI Proxy" em Credenciais › OAuth

## Login
- ✅ 27 · Ícone do botão de login SSO igual ao da aplicação da sessão "Set Alvesmaia"
  - ✅ sessão offline (pergunta enviada); botão lido no código: alvesmaia-gateway e automation-portal (logo 4 quadrados, botão claro)
  - ✅ hub: botão secundário claro, logo 18px (teste test_sso_button_is_light_so_the_microsoft_logo_keeps_its_colors)
  - ✅ logo sem contorno (regra genérica .secondary-button svg); ui-sweep 0 achados; deploy na :8090

## Textos
- ✅ 28 · Revisar travessões (—) nos textos da interface, a começar pela tela de consentimento OAuth
  - ✅ catálogo (59 pares reescritos, 6 fragmentos órfãos removidos), 5 templates, 36 textos em .py, 10 em JS
  - ✅ guarda test_no_em_dash.py; "—" sozinho como célula vazia mantido; ui-sweep 0 achados; deploy na :8090

## Fechamento
- ✅ 21 · Integração, E2E das telas, ui-sweep, deploy na :8090, limpeza
  - ✅ hub-check --all verde; E2E completo na instância isolada (141 + specs corrigidos 16/16)
  - ✅ ui-sweep --quick: 0 achados (docs logadas voltaram a seguir o tema da conta)
  - ✅ hub_update sem parada; 4 apps reconstruídos (pino keep-20261015b), ready 200, sem avisos de operation id
