# Keyboard-only walkthrough (wave 9)

Manual keyboard accessibility walkthrough for the hub (WCAG 2.2 AA, criteria
2.1.1 Keyboard, 2.1.2 No keyboard trap, 2.4.1 Bypass blocks, 2.4.3 Focus order, 2.4.7/2.4.11
Focus visible, 2.4.13 Focus appearance, 2.5.8 Target size, 4.1.3 Status messages).
Run on 28/09/2026 in the compose stack (Chromium 1440×900, light and dark themes, pt-BR and en),
driving the browser **with the keyboard only** through Playwright (the automatable steps became the
"accessibility" scenario and the other wave scenarios in `tests-e2e/tests/10-onda9.spec.ts`) and
checking the focus ring in the screenshots. No test with real screen readers (NVDA/JAWS/VoiceOver)
— recorded in PENDENCIAS.

| # | Where | Keys | Expected | Result |
|---|---|---|---|---|
| 1 | Any page | `Tab` (first) | "Pular para o conteúdo" (Skip to content) appears at the top; `Enter` moves focus to the `<main>` | ✅ |
| 2 | Header | `Tab` … | Order: brand → search (Ctrl K) → Inbox → user menu; 3 px focus ring in the brand color on all | ✅ |
| 3 | Search | `Ctrl+K` (or `Enter` on the button) | Modal dialog; focus in the field; `↓`/`↑`/`Home`/`End` move the active option (`aria-activedescendant`), `Enter` opens, `Esc` closes and **returns focus** to whoever opened it; `Tab` stays trapped in the dialog (field ↔ Fechar) | ✅ |
| 4 | Inbox (bell) | `Enter`/`Espaço` on the bell | Popover opens; `Tab` goes through items and actions; `Esc` closes and returns to the bell; opening another menu closes this one | ✅ |
| 5 | User menu | `Enter`, `Tab` | Theme (Sistema/Claro/Escuro, i.e. System/Light/Dark, `aria-pressed`) and language (select + OK) by keyboard; the page reloads with the chosen theme/language and focus returns to the top | ✅ |
| 6 | Rail | `Tab` | Início (Home), Aplicações (Applications), Status, Administração (Administration) (`aria-expanded`, `aria-controls`); current item with `aria-current="page"` | ✅ |
| 7 | Início (Home) | `Tab` until "Personalizar" (Customize), `Enter` | Edit mode; on each widget ↑/↓/✕ (labels "Mover para cima · Inbox"…, i.e. Move up · Inbox…); moving announces "Widget movido para a posição N" (Widget moved to position N) (`aria-live`) and keeps focus on the button; ✕ returns focus to "Personalizar"; "Adicionar widget" (Add widget) with select | ✅ |
| 8 | Tour | first access to Início | Dialog with focus on "Próximo" (Next); `→`/`←` navigate, `Esc` stops (resumed on the next access), `Tab` cycles only between the tour buttons; target highlighted with an outline | ✅ |
| 9 | Applications | `Tab` | Favorite star (`aria-pressed`, label "Fixar X nos favoritos" (Pin X to favorites)), "Abrir aplicação" (Open application), "Solicitar acesso" (Request access) (apps outside the list) | ✅ |
| 10 | Inbox (page) | `Tab` | Filters (`aria-current`); per item: deep-link link, comment (with label), Aprovar/Recusar (Approve/Decline), Marcar como lida (Mark as read), Arquivar (Archive); preferences in a table with headers | ✅ |
| 11 | App (fragment) | `Tab` in the sidebar, `Enter` on a section | Section change moves focus to the new page title (read by screen readers); sidebar with `aria-current` (Python/SDK apps) | ✅ |
| 12 | Administration/Operations modals | `Enter` on the button, `Tab`, `Esc` | Open with focus on the first field; `Tab` stays trapped in the dialog (generic in `hub-ux.js` for every modal `role=dialog`); on close, focus returns to the button that opened it | ✅ |
| 13 | Motion | system with "reduce motion" | Spinner, transitions and animations off | ✅ (global rule in `hub.css`) |
| 14 | Targets | — | Icon buttons, filters and menu options ≥ 24×24 px | ✅ |

Failures found and fixed during the walkthrough: script order (Inbox actions with no effect),
contrast of grays and of the apps' own colors (in both themes), missing labels in the
`ativos` Settings fields, 404 without title/language, inverted chrome in the dark theme.
