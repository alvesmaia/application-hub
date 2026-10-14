# Screen patterns (`data-hub-*`)

Apps have no JavaScript of their own. The hub interprets attributes in the HTML of the fragments.

| Attribute | What it does |
|---|---|
| `data-hub-get` / `data-hub-post` | fetches or sends and inserts the response (`data-hub-target`, `data-hub-swap`) |
| `data-hub-trigger` | `click`, `submit`, `change`, `input`, `load`, `every 5s` |
| `data-hub-confirm` | confirmation before the action |
| `data-hub-modal` | opens a `<template>` in a hub modal |
| `data-hub-toast` | short message after the action |
| `data-hub-filter` | filters the rows of a table |
| `data-hub-table`, `data-hub-sort` | pagination and sorting on the client |
| `data-hub-copy`, `data-hub-copy-from` | copy to the clipboard |
| `data-hub-tabs`, `data-hub-tab` | accessible tabs |
| `data-hub-validate` | validation messages next to the field |
| `data-hub-chart` | hub chart |
| `data-hub-sse`, `data-hub-ws` | real-time via SSE or WebSocket, with fallback polling ([Real-time](/developers/guias/tempo-real)) |

Example:

```html
<form data-hub-get="{{ base }}/lista" data-hub-target="#lista" data-hub-swap="innerHTML" data-hub-trigger="input">
  <input name="q" aria-label="Buscar">
</form>
<div id="lista"></div>
```

`data-hub-confirm="Excluir o cartão?"` goes on the `<form>` or on the submit button (also on links and on buttons with
`data-hub-post`). The hub shows its own dialog before sending.

Ready-made hub classes: buttons `primary-button`, `secondary-button`, `danger-button` and `icon-button`;
panels `hub-panel` and `hub-panel-head`; forms `hub-form-grid`, `form-field` and `hub-actions`; notices
`hub-notice` (+ `hub-notice-error`); tables `data-table` inside `hub-table-scroll`; empty `empty-state`. The
model from `hub project create` uses all of them. Colors only through tokens (`var(--…)`).

Full list and rules: Contract §25 ([Contract](/developers/contrato)).

## Test without a browser

The right way is to open the screen in the hub (`hub dev` and then `/apps/<id>`). For a quick test from the command line,
call the **fragment**, not the page: the hub's forms send to `/_frag/<id>/…`. The page `/apps/<id>/…`
does not accept POST (`405`), and the fragment requires the headers the shell sends — without them you get `403`, and a POST
without an `Idempotency-Key` of 8 to 128 characters gets `400`.

```bash
hub dev &                                     # http://localhost:4173
H='-H Origin:http://localhost:4173 -H Sec-Fetch-Site:same-origin -H X-Requested-With:XMLHttpRequest'
curl -s $H http://localhost:4173/_frag/<id>/                      # initial fragment
K="-H Idempotency-Key:$(date +%s)-teste"      # POST: key of 8 to 128 characters
curl -s $H $K -d "name=Arroz&quantity=2" http://localhost:4173/_frag/<id>/itens
```

Replace `<id>` with the app id. The `hub check` conformance uses exactly these routes.
