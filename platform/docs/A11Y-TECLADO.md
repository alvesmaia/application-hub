# Roteiro só com teclado (onda 9)

Roteiro de verificação manual de acessibilidade por teclado do hub (WCAG 2.2 AA, critérios
2.1.1 Teclado, 2.1.2 Sem armadilha de foco, 2.4.1 Pular blocos, 2.4.3 Ordem do foco, 2.4.7/2.4.11
Foco visível, 2.4.13 aparência do foco, 2.5.8 Tamanho do alvo, 4.1.3 Mensagens de status).
Executado em 28/09/2026 no compose (Chromium 1440×900, tema claro e escuro, pt-BR e en),
conduzindo o navegador **só pelo teclado** com o Playwright (os passos automatizáveis viraram o
cenário "acessibilidade" e os demais cenários da onda em `tests-e2e/tests/10-onda9.spec.ts`) e
conferindo o anel de foco nas capturas. Sem teste com leitores de tela reais (NVDA/JAWS/VoiceOver)
— registrado em PENDENCIAS.

| # | Onde | Teclas | Esperado | Resultado |
|---|---|---|---|---|
| 1 | Qualquer página | `Tab` (primeiro) | "Pular para o conteúdo" aparece no topo; `Enter` leva o foco ao `<main>` | ✅ |
| 2 | Header | `Tab` … | Ordem: marca → busca (Ctrl K) → Inbox → menu do usuário; anel de foco de 3 px na cor da marca em todos | ✅ |
| 3 | Busca | `Ctrl+K` (ou `Enter` no botão) | Diálogo modal; foco no campo; `↓`/`↑`/`Home`/`End` movem a opção ativa (`aria-activedescendant`), `Enter` abre, `Esc` fecha e **devolve o foco** a quem abriu; `Tab` fica preso no diálogo (campo ↔ Fechar) | ✅ |
| 4 | Inbox (sino) | `Enter`/`Espaço` no sino | Popover abre; `Tab` percorre itens e ações; `Esc` fecha e volta ao sino; abrir outro menu fecha este | ✅ |
| 5 | Menu do usuário | `Enter`, `Tab` | Tema (Sistema/Claro/Escuro, `aria-pressed`) e idioma (select + OK) por teclado; a página recarrega no tema/idioma escolhido e o foco volta ao topo | ✅ |
| 6 | Rail | `Tab` | Início, Aplicações, Status, Administração (`aria-expanded`, `aria-controls`); item atual com `aria-current="page"` | ✅ |
| 7 | Início | `Tab` até "Personalizar", `Enter` | Modo de edição; em cada widget ↑/↓/✕ (rótulos "Mover para cima · Inbox"…); mover anuncia "Widget movido para a posição N" (`aria-live`) e mantém o foco no botão; ✕ devolve o foco a "Personalizar"; "Adicionar widget" com select | ✅ |
| 8 | Tour | primeiro acesso ao Início | Diálogo com foco em "Próximo"; `→`/`←` navegam, `Esc` interrompe (retomado no próximo acesso), `Tab` circula só entre os botões do tour; alvo destacado com contorno | ✅ |
| 9 | Aplicações | `Tab` | Estrela de favorito (`aria-pressed`, rótulo "Fixar X nos favoritos"), "Abrir aplicação", "Solicitar acesso" (apps fora da lista) | ✅ |
| 10 | Inbox (página) | `Tab` | Filtros (`aria-current`), por item: link do deep link, comentário (com rótulo), Aprovar/Recusar, Marcar como lida, Arquivar; preferências em tabela com cabeçalhos | ✅ |
| 11 | App (fragmento) | `Tab` na sidebar, `Enter` numa seção | Troca de seção leva o foco ao título da página nova (leitor anuncia); sidebar com `aria-current` (apps Python/SDK) | ✅ |
| 12 | Modais da Administração/Operações | `Enter` no botão, `Tab`, `Esc` | Abrem com o foco no primeiro campo; `Tab` fica preso no diálogo (genérico no `hub-ux.js` para todo `role=dialog` modal); ao fechar, o foco volta ao botão que abriu | ✅ |
| 13 | Movimento | sistema com "reduzir movimento" | Spinner, transições e animações desligadas | ✅ (regra global em `hub.css`) |
| 14 | Alvos | — | Botões de ícone, filtros e opções do menu ≥ 24×24 px | ✅ |

Falhas encontradas e corrigidas durante o roteiro: ordem dos scripts (ações da Inbox sem efeito),
contraste de cinzas e das cores próprias dos apps (nos dois temas), rótulos ausentes nos campos
de Configurações do `ativos`, 404 sem título/idioma, cromo invertido no tema escuro.
