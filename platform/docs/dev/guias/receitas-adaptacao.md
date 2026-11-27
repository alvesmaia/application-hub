# Conversion recipes: JavaScript to `data-hub-*`

For agents adapting an existing server-rendered app (HTML + its own JS). Apps run **no JavaScript of their own**:
every behavior becomes a `data-hub-*` attribute (full table: contract §25). Keep the original classes and markup;
only the wiring changes. Rule of thumb: find the JS with the grep in each recipe, port the behavior, delete the
script. The examples come from a real port (tickets, wiki, board, admin lookups); names are generic.

Always build URLs with `{{ base }}`. New texts go in the catalog (pt-BR and en).

## 1. Split button ("New ▾") and "⋮" menus: `data-hub-dropdown`

**Recognize:** `aria-haspopup`, `aria-expanded`, `menu.hidden =`, a `document.addEventListener('click'` that closes it.

Original:
```html
<button class="wiki-new-caret" id="caret" aria-haspopup="true" aria-expanded="false">▾</button>
<div class="wiki-new-menu" id="menu" hidden>…</div>
<script>
caret.addEventListener('click', e => { e.stopPropagation(); menu.hidden = !menu.hidden;
  caret.setAttribute('aria-expanded', !menu.hidden); });
document.addEventListener('click', e => { if (!menu.contains(e.target)) menu.hidden = true; });
</script>
```
Ported (markup and classes unchanged, script gone):
```html
<button class="wiki-new-caret" id="caret" data-hub-dropdown="#menu"
        aria-haspopup="true" aria-expanded="false">▾</button>
<div class="wiki-new-menu" id="menu" role="menu" hidden>…</div>
```
Per-row menus need a unique id: `data-hub-dropdown="#rowMenu-{{ r.id }}"`.

**CSS:** the kit only toggles `hidden`/`aria-expanded`; the menu's position stays the app's CSS. If the original
positioned it from JS (`getBoundingClientRect`, `style.top`), replace that with CSS:
```css
.kb-cell { position: relative; display: inline-block; }
.kb-cell .kb-menu { position: absolute; right: 0; top: calc(100% + 4px); z-index: 60; }
.kb-menu[hidden] { display: none; }
```
**Pitfall:** a menu inside an element with `overflow: hidden` is clipped (the original floated it with JS to
`<body>`); give the row `position: relative` and let the menu overflow, or drop the clipping. Items that open a
modal or post a form keep working (a click on an item closes the menu).

## 2. Kanban drag and drop with a placeholder: `data-hub-drag` / `data-hub-dropzone`

**Recognize:** `addEventListener('dragstart'`, `'dragover'`, `'drop'`, `draggable="true"`, `.is-dragging`,
`.drop-target`, `insertBefore(indicator`, `new FormData()` + `fetch('/…/reorder'`.

Original: ~80 lines (dragstart, dragover with a `cardAfter(y)` helper, an indicator element, drop, then a `fetch`
posting `new_state` and `before_id`).

Ported:
```html
<div class="kanban-col" data-hub-dropzone data-hub-drop-indicator="kbv-drop-indicator"
     data-hub-drop-name="new_state" data-hub-drop-value="{{ col }}" data-hub-drop-body=".kanban-col-body">
  <div class="kanban-col-body">
    <div class="kanban-card" draggable="true"
         data-hub-drag="{{ base }}/board/{{ k.id }}/reorder" data-hub-drag-id="{{ k.id }}">…</div>
  </div>
</div>
```
The kit moves the card before the item under the pointer and posts `new_state=<col>&before_id=<id>` (`""` = end)
to the card's URL; a refused answer (non-2xx) puts it back. Server:
```python
@router.post("/board/{task_id}/reorder")
def board_reorder(task_id: int, new_state: str = Form(...), before_id: str = Form(""), ...):
    ...  # move + reindex sort_order of the column; 4xx if the move is not allowed
    return Response(status_code=204)
```
**CSS mapping:**

| Original | Kit |
|---|---|
| `.is-dragging` on the card | `.hub-dragging` |
| `.drop-target` on the column | `.hub-drop-over` |
| the indicator element | `data-hub-drop-indicator="app-class"` on the list; the kit gives it the dragged item's height |

```css
.kanban-v2 .kbv-card.hub-dragging { display: none; }              /* was .is-dragging */
.kanban-v2 .kbv-col.hub-drop-over .kbv-col-body { outline: 1.5px solid var(--accent-primary); }
.kanban-v2 .kbv-drop-indicator { flex: 0 0 auto; min-height: 40px; border: 1px dashed var(--border-hover); }
```
**Pitfalls:** (a) a move that needed a prompt/modal first (e.g. "block reason" when dropping into Blocked) becomes
`data-hub-drop-confirm="Text"` on that column, or the server refuses and the card returns; do not keep the
`window.prompt`. (b) Put `data-hub-drop-body` on the scrolling container, not the column, or the placeholder lands
in the header. (c) A card is a link: keep the `<a>` inside the draggable `<div>`, never make the `<a>` the draggable.

## 3. Nested drag (subtasks inside a card): groups

**Recognize:** a second set of `dragstart` listeners next to the first, guards like `if (!dragged) return`.

Ported: pair item and list with the same group name so card drags and subtask drags never mix:
```html
<div class="kanban-cl-items" data-hub-dropzone data-hub-drop-indicator="sub-drop-indicator"
     data-hub-drop-group="sub-{{ task.id }}" data-hub-drop-name="parent" data-hub-drop-value="{{ task.id }}">
  <div class="kanban-cl-item" draggable="true" data-hub-drag="{{ base }}/board/subtask/{{ s.id }}/reorder"
       data-hub-drag-id="{{ s.id }}" data-hub-drag-group="sub-{{ task.id }}">…</div>
</div>
```
The group is per parent (`sub-{{ task.id }}`), so a subtask only lands in its own card's list. Style `.hub-dragging`
and `.hub-drop-over` on the nested classes too.
**Pitfall:** a drop on the nested list must go to that list, not the column around it (the kit's innermost list
wins); without groups the column would take the subtask and post a `new_state` for it.

## 4. Collapsible column/panel remembered in localStorage

**Recognize:** `localStorage.getItem('…collapsed')`, `classList.toggle('is-collapsed')`, an inline `<style>` injected
in `<head>` to avoid the flash.

Ported (no script, no pre-paint style):
```html
<div class="kbv-col{{ ' is-collapsed' if col == 'blocked' else '' }}"
     data-hub-state-class="is-collapsed" data-hub-remember="board.collapsed.{{ col }}">
  <button class="kbv-strip" data-hub-class="remove">…</button>   <!-- collapsed strip: expand -->
  <span class="kbv-collapse" data-hub-class="add">‹</span>       <!-- header chevron: collapse -->
</div>
```
`data-hub-class` is `add`, `remove` or `toggle`. **The server renders the default class** (here Blocked starts
collapsed); the browser keeps the user's choice per app and reapplies it to every new fragment, so there is no flash
of the wrong state. The click goes no further: a chevron inside a link or card does not open it.
**Pitfall:** keep the original CSS for `.is-collapsed`; do not also keep the `localStorage` code (two sources of
truth). Remember keys must be unique per element (`…{{ col }}`).

## 5. Spreadsheet-like header filters and sort: `data-hub-colfilter`

**Recognize:** `table-autofilter`-style scripts, a funnel icon per `<th>`, `sort` handlers, `<input>`s above the
table filtering a column.

Ported:
```html
<table class="data-table" id="ticketsTable" data-hub-colfilter data-hub-colfilter-empty="{{ t('No results') }}">
  <thead><tr>
    <th>#</th>
    <th data-hub-col="title"  data-hub-col-label="{{ t('Title') }}">{{ t("Title") }}</th>
    <th data-hub-col="status" data-hub-col-label="{{ t('Status') }}">{{ t("Status") }}</th>
    <th data-hub-col="due" data-hub-col-label="{{ t('Due') }}" data-hub-col-type="date">{{ t("Due") }}</th>
  </tr></thead>
  <tbody><tr><td>…</td><td data-hub-value="1766188800000">20/11</td></tr></tbody>
</table>
```
Mark the **same columns** the original filtered, in the header (a funnel in each `<th>`: sort, the column's values
with search and "Select all", or a date range). State lives in the URL (`f.<key>=a,b`, `sort=<key>:asc|desc`). A
cell's value is its text, or `data-hub-value` (use it when the text is decorated: badges, avatars; dates in ms).
Never replace the header filters with filter fields above the table.

## 6. Modal with a form, 422 and PRG 303

**Recognize:** `showModal()`, `.modal.open`, `classList.add('is-open')`, `fetch(…, {method:'POST'})` plus manual
error rendering, `location.reload()`.

Ported: the modal is a `<template>` opened by `data-hub-modal`; a plain form (post, 303 back to the list) needs no
more attributes:
```html
<button data-hub-modal="#newItem">New</button>
<template id="newItem" data-hub-modal-labelledby="newItem-t">
  <form method="post" action="{{ base }}/items">
    <h3 id="newItem-t">{{ t("New item") }}</h3>
    <input name="name" required maxlength="50" class="form-control">
    <button type="button" data-hub-close>{{ t("Cancel") }}</button>
    <button type="submit">{{ t("Save") }}</button>
  </form>
</template>
```
Server: success returns `RedirectResponse(f"{public_base(req)}/items", status_code=303)`. When the original showed
validation errors inside the modal, post through the kit so a 422 body replaces the form without a reload:
`<form id="newItem-form" data-hub-post="{{ base }}/items" data-hub-target="#newItem-form" data-hub-swap="outerHTML">`
and have the route answer **422 with the re-rendered form** (a body on any status replaces the target).
**Pitfalls:** Esc, `[data-hub-close]`, `.modal-cancel` and a click outside already close it: delete the original
close handlers. One `<template>` per row (`#edit-{{ r.id }}`) is fine for edit modals. Validate with Pydantic, not a
raw dict. Check the 303/422 flow in a scenario test.

## 7. Confirm before delete: `data-hub-confirm*`

**Recognize:** `confirm(`, `onsubmit="return confirm`, a custom confirm modal with a promise.
```html
<form method="post" action="{{ base }}/items/{{ i.id }}/delete"
      data-hub-confirm="{{ t('Delete') }} {{ i.name }}?" data-hub-confirm-title="{{ t('Delete item') }}"
      data-hub-confirm-label="{{ t('Delete') }}" data-hub-confirm-danger>
  <button type="submit" class="kb-item kb-danger">{{ t("Delete") }}</button>
</form>
```
Works on links, buttons and forms; `-danger` makes the button red. **Pitfall:** put it on the `<form>` (or the
submit button), never on a wrapper `<div>`; the original's confirm-modal markup is deleted, not ported.

## 8. Filter on change and live text filter

**Recognize:** `onchange="this.form.submit()"`, `addEventListener('change'` + `form.submit()`;
`addEventListener('input'` hiding rows with `style.display`.
```html
<form method="get"><select name="status" data-hub-autosubmit>…</select></form>      <!-- submits on change -->

<input data-hub-filter="#list" data-hub-filter-empty="#nothing" placeholder="{{ t('Filter') }}">
<div id="list">
  <div class="row" data-hub-filter-item data-hub-filter-text="{{ r.name }}">…</div>
</div>
```
`data-hub-filter` ignores accents and case and filters the `tbody tr` or `[data-hub-filter-item]` of the container
(by id). `data-hub-autosubmit` goes on the `<select>` inside a form; a `GET` form keeps the filter in the URL.
**Pitfall:** `data-hub-filter` only sees rows in the DOM; with server pagination it filters one page (see the end).

## 9. Lazy-loaded fragment (a big option list)

**Recognize:** `fetch(` when a `<details>`/select opens, `innerHTML =` with the response.
```html
<details class="ss-select"><summary class="ss-toggle">…</summary>
  <div class="ss-panel">
    <div class="ss-options" data-hub-get="{{ base }}/tickets/{{ t.id }}/initiative-options"
         data-hub-trigger="load" data-hub-swap="innerHTML"></div>
  </div>
</details>
```
The route returns only the options HTML; options that submit are `<button type="submit" name=… value=…>` inside the
surrounding form. The URL must be inside the app (`/apps/{id}/…`). Use `every 5s` to poll.
**Pitfall:** `load` fires on mount, not when the `<details>` opens, so the list is fetched up front.

## 10. Tabs

**Recognize:** `role="tablist"`, `aria-selected` toggled in JS, `hidden` swapped on panels.
```html
<div role="tablist" data-hub-tabs>
  <button role="tab" data-hub-tab="#panel-a" aria-selected="true">A</button>
  <button role="tab" data-hub-tab="#panel-b" aria-selected="false">B</button>
</div>
<div id="panel-a" role="tabpanel">…</div><div id="panel-b" role="tabpanel" hidden>…</div>
```
Arrows, Home/End and `aria-selected` are handled by the kit. The server renders which tab starts selected.

## 11. Client-side markdown: render on the server

**Recognize:** `marked.parse(`, `data-md`, a sanitizer script, a CDN `<script>`.

Original: `<div class="dc-text" data-md>{{ body }}</div>` plus the markdown and sanitizer scripts.
Ported: render in Python (e.g. `markdown-it-py`, sanitized with `nh3`) and output the HTML:
```html
<div class="dc-text">{{ body | markdown }}</div>
```
Register `markdown` as a Jinja filter returning sanitized `Markup`; never `| safe` on raw input. Keep the original
classes so the CSS applies. **Pitfall:** a live preview in an editor (typing updates a pane) has no kit equivalent:
use `data-hub-post` with `data-hub-trigger="input"` to a preview route, or drop it and list it in the report.
Diagram renderers (mermaid) are the same case: pre-render or list as a gap.

## 12. Theme selectors

**Recognize:** `theme.js`, `data-theme="light|dark|system"`, `prefers-color-scheme`.

Keep the original's palette and selectors. The hub sets `data-theme` to `light`, `dark` or **`auto`** (follow the
system) on `<html>`. Add `auto` **next to the original's "system" selector**; never add light/dark to another block:
```css
:root[data-theme="system"], :root[data-theme="auto"] { /* the original "system" rules */ }
@media (prefers-color-scheme: dark) {
  :root[data-theme="system"], :root[data-theme="auto"] { /* the original dark values */ }
}
```
Drop the theme-switcher script (the hub owns the choice).

## 13. Full-height page whose columns scroll inside

**Recognize:** `height: calc(100vh - …)`, `overflow: auto` on `.layout-content`/`main`, a board that fills the
viewport.

The hub's content area is `.workspace-content` (already the viewport's height below the hub header, and it
scrolls). Map the original's page-level rule to it, scoped with `:has()` so other pages are untouched:
```css
.workspace-content:has(.kanban-v2) { display: flex; flex-direction: column; padding-bottom: 0; box-sizing: border-box; }
```
Remove `100vh` arithmetic that assumed the app's own header. **Pitfall:** without `padding-bottom: 0` the board
shows a double scrollbar.

## Other JS to delete

Sidebar collapse, theme toggle, session renew, SPA navigator, avatar and header scripts: the hub owns them. Phone
sidebar: `data-hub-menu="open|close"`. Toasts: `data-hub-toast="…"` or the `X-Hub-Toast` header. Copy:
`data-hub-copy`. Dependent selects: `data-hub-depends` + `data-hub-options`. Real time: `data-hub-ws`/`data-hub-sse`.

## Not covered by the kit yet

- Combobox with search (type to filter a big select and pick).
- Multi-select filter (checkbox list with chips).
- Server-side pagination combined with header filters (`data-hub-colfilter` and `data-hub-filter` work on the rows
  in the page).

Meanwhile keep the closest equivalent that needs no app JavaScript (`<details>` plus lazy `data-hub-get` options,
`<select multiple>`, a `GET` form with `data-hub-autosubmit`, a larger page size) and **list each substitution in
the final report**, so the owner can ask for the kit feature.
