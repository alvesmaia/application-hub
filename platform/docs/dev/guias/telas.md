# Padrões de tela (`data-hub-*`)

Apps não têm JavaScript próprio. O hub interpreta atributos no HTML dos fragmentos.

| Atributo | O que faz |
|---|---|
| `data-hub-get` / `data-hub-post` | busca ou envia e insere a resposta (`data-hub-target`, `data-hub-swap`) |
| `data-hub-trigger` | `click`, `submit`, `change`, `input`, `load`, `every 5s` |
| `data-hub-confirm` | confirmação antes da ação |
| `data-hub-modal` | abre um `<template>` num modal do hub |
| `data-hub-toast` | mensagem curta depois da ação |
| `data-hub-filter` | filtra linhas de uma tabela |
| `data-hub-table`, `data-hub-sort` | paginação e ordenação no cliente |
| `data-hub-copy`, `data-hub-copy-from` | copiar para a área de transferência |
| `data-hub-tabs`, `data-hub-tab` | abas acessíveis |
| `data-hub-validate` | mensagens de validação junto do campo |
| `data-hub-chart` | gráfico do hub |
| `data-hub-sse`, `data-hub-ws` | tempo real por SSE ou WebSocket, com polling de reserva ([Tempo real](/developers/guias/tempo-real)) |

Exemplo:

```html
<form data-hub-get="{{ base }}/lista" data-hub-target="#lista" data-hub-swap="innerHTML" data-hub-trigger="input">
  <input name="q" aria-label="Buscar">
</form>
<div id="lista"></div>
```

`data-hub-confirm="Excluir o cartão?"` vai no `<form>` ou no botão de envio (também em links e em botões com
`data-hub-post`). O hub mostra o diálogo dele antes de enviar.

Classes prontas do hub: botões `primary-button`, `secondary-button`, `danger-button` e `icon-button`;
painéis `hub-panel` e `hub-panel-head`; formulários `hub-form-grid`, `form-field` e `hub-actions`; avisos
`hub-notice` (+ `hub-notice-error`); tabelas `data-table` dentro de `hub-table-scroll`; vazio `empty-state`. O
modelo do `hub project create` usa todas. Cores só por tokens (`var(--…)`).

Lista completa e regras: Contrato §25 ([Contrato](/developers/contrato)).
