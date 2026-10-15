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
- ▶️ 20 · /admin/env: remover a seção "por aplicação"; tabela de variáveis ganha coluna Aplicação com filtro dropdown de múltipla seleção

## Inbox
- ✅ 22 · Filtros em dropdown e paginação
  - ✅ botões de filtro viram um select "Mostrar" (mesmos ?filter=, autosubmit)
  - ✅ lista paginada (pager mantém o filtro; limite de 200 removido)

## Header
- ▶️ 23 · Ícone do GitHub no header (HUB_SOURCE_URL; padrão = repositório oficial; vazio esconde)

## Fechamento
- ⬜ 21 · Integração, E2E das telas, ui-sweep, deploy na :8090, limpeza
