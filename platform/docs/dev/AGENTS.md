# Instructions for agents

You are a coding agent. Follow these steps to create and publish an app on this Application Hub
(`@HUB@`). If the request is a **new** app and does not say what it does, ask before starting. If the app
**already exists**, read [Adapting an existing app](#adapting-an-existing-app) first: the goal is the same app,
with the same pages, URLs, layout, design and behavior, now inside the hub.

`@HUB@` is this hub's address (the hub fills it in when it serves this page). Reading this file from the
repository instead? Ask the user for the hub's address; with no hub at hand, build the CLI from the repository
(step 1).

## 1. Install the CLI

Prerequisite: **uv** and Python 3.12. The CLI comes from this hub's public index:

```bash
uv tool install --reinstall hub-cli --index @HUB@/dev/cli/simple/
hub --version   # hub-cli … (hub X, hub-sdk …): X must be the hub's version (@HUB@/healthz)
```

Behind a corporate proxy that inspects TLS (the hub's certificate comes from a company CA): install with
`uv tool install --native-tls …` (uv then uses the system's certificate store). The CLI trusts the system store and
the public CAs; if the company CA is not in the system store, point `SSL_CERT_FILE` to a `.pem` file with it. Never
turn certificate checks off. A refused certificate shows "certificado TLS do hub não confiável" with the reason.

`--reinstall` matters: an older CLI can keep the same `hub-cli` number while missing newer SDK features. The
`hub X` part tells which hub it came from. Without a hub, from a clone of the repository:
`bash platform-py/scripts/build-cli.sh && uv tool install --reinstall platform-py/dist/cli/hub_cli-*.whl`.

## 2. Create the project

Choose an id (2–32 characters: `a-z`, `0-9`, `-`) and generate the project without prompts:

```bash
hub project create <id-do-app> --stack python-fastapi --yes --json
cd <id-do-app> && uv sync
```

## 3. Read the rules

- The `AGENTS.md` generated at the project root: structure, commands and template rules.
- The app contract: [@HUB@/dev/docs/contrato.md](@HUB@/dev/docs/contrato.md) — what the hub requires of every app.
- References: [CLI](@HUB@/dev/docs/cli.md) and [SDK](@HUB@/dev/docs/sdk.md).

## 4. Develop

- No app JavaScript of your own: screen behaviors use `data-hub-*` attributes.
- Links always use the app's public base (`{{ base }}` in the templates), never a raw `/<id>/…`.
- Every new screen and route comes with a test.

## 5. Verify

```bash
hub check   # lint, types, tests, manifest, openapi.json and conformance
hub dev     # local hub with the app in the shell: http://localhost:4173/apps/<id-do-app>
```

Repeat until `hub check` is green.

## 6. Publish

Sign the CLI in once: `hub login` prints a link and a code. **Show both to the user** (they approve in the browser,
signed in to the hub); the CLI then acts as that person, with their permissions, for every command. Whenever a
command answers "não autenticado" or "a sessão do CLI expirou", run `hub login` again and relay the link and code.

```bash
hub login --hub @HUB@
hub deploy --env staging --yes
```

CI (no one to approve): a personal token with the **upload** and **status** actions from My account › Access in
`HUB_TOKEN`.

Without access to the hub, generate the package and deliver the `.hubapp` file:

```bash
hub pack --build-dir .
```

Whoever imports it uses Operations › Deployments › Import package in the hub: choose the `.hubapp`, check the preview
(id, version, what it updates), fill in the `requires.variables`/`requires.secrets` fields (empty = keep the hub's
value), choose the environment and deploy. Step by step in the [Publishing guide](/developers/guias/publicar).

### Through the hub MCP (no CLI token)

If you are connected to `@HUB@/mcp` (OAuth or a personal token with the scopes `mcp` and `api:write`) but have no
CLI token: run `hub pack --build-dir .`, call the MCP tool `deployments_upload` (`env` = `staging` or `producao`) and
run the `curl` it returns with the `.hubapp` (one-time link, 10 minutes). Follow with `jobs_get` and
`deployments_list`; production waits for `deployments_approve`, which asks for a `confirm_token`: confirm with the
user before calling again. Step by step in [Publish and operate through MCP](/developers/guias/mcp-api).

## Adapting an existing app

The person expects to open the hub and find **their app as it was**: the same pages, URLs, data, layout,
look and behavior. Rewriting it in the template's look, leaving pages out or "improving" its CSS is a failure
even when `hub check` is green. `hub check` and `hub compare` hold you to this — read their messages and fix
what they say; never lower a limit or tick an item you did not do.

### Order of work

1. **Inventory** (`port-inventory.md`, below) with the original running.
2. **Skeleton:** create the project (step 2) next to the original, copy the CSS (`port.json`), fonts, images and
   the sidebar; port **one** page and make it pass `hub compare` (style, structure and components).
3. **The other pages**, one by one, each passing `hub compare` before the next.
4. **Behaviors:** every JavaScript behavior of the inventory, tried by hand on both sides (drag, menus,
   collapse, filters, forms valid and invalid, delete).
5. **Done** (definition below), then publish to staging and write the report.

### 1. Inventory — `port-inventory.md`

At the project root, a checklist `hub check` reads: `- [x]` done, `- [~] … — reason` a deviation (the reason is
required: what the hub lacks or what you chose), `- [ ]` still to do (`hub check` fails while one is left).
List, from the running original:

- **Routes:** every page, including nested ones (`/tickets/{id}`, `/wiki/new`), with what it shows.
- **Behaviors:** every piece of the original's JavaScript, with the hub equivalent you will use. Find them in
  the templates and static JS with
  `grep -rnE "addEventListener|onclick=|fetch\(|localStorage|draggable|classList|aria-expanded|showModal|new Chart"`.
  One line each: `- [x] board: drag cards between columns → data-hub-drag`.
- **States:** what the page shows without a click — a column collapsed by default, an open tab, a selected
  filter, an empty state.
- **Data:** the same records on both sides (point the port at a copy of the original's database in `hub dev`).

### 2. The original CSS stays untouched — `port.json`

Copy each original stylesheet byte for byte into the project (e.g. `assets/original/`) and put **every** change
the hub needs (theme selector additions, `.workspace-content` rules, removing the top bar) in one overrides file.
`port.json` at the root:

```json
{
  "original": "../my-original-app",
  "css": [["app/static/css/themes.css", "src/my_app/assets/original/themes.css"],
          ["app/static/css/components.css", "src/my_app/assets/original/components.css"]],
  "app_css": "src/my_app/assets/app.css",
  "overrides": "src/my_app/assets/hub-overrides.css"
}
```

`hub port css` writes `app_css` (the copies in cascade order, then the overrides); `hub check` fails when a copy
differs from the original or `app_css` was edited by hand. A Tailwind app keeps its build: its output is the
"original" CSS, rebuilt with the `content` paths pointing at the new templates.

### 3. Return fragments, keep the layout and the markup

- **Fragments:** drop the app's own `<html>`/`<head>`/`<body>` and top bar: the page is the `workspace`
  fragment (§3 of the [contract](@HUB@/dev/docs/contrato.md)).
- **Markup:** each page template keeps the original page's HTML (the same elements, classes and text) inside
  `{% block content %}`, wrapped in an element with **`data-hub-original`**; the original sidebar's HTML goes in
  `{% block sidebar %}` of `workspace.html`, also inside `data-hub-original` (the hub keeps only its collapse
  button, back link and phone drawer around it). `data-hub-original` keeps the hub's element defaults (heading
  weight and others) out of the original markup. `NavItem`/`AppInfo` remain for the default sidebar of new apps.
- **URLs:** each route keeps its path under `/{id}/…`; nested paths work. Build links with `{{ base }}` (the
  public base, from `hub_sdk.render.public_base`); never flatten routes to `?id=`.
- **Top bar:** what it showed goes where the hub already has it — never a second header inside the app:

  | The app's top bar had | In the hub |
  |---|---|
  | search box (global) | the hub has no global search: keep the app's search as a page of its own (a sidebar item) or, inside a list, as its filter bar (`data-hub-filter` or a `GET` form) |
  | notifications bell, unread counter | the hub's Inbox: `hub_sdk.outbox.notification(user, title, link=…, to_roles=[…] or to_users=[…])` (§20; empty = only the user who caused it), written with `sdk.outbox.in_transaction(conn, ev)` in the action's transaction; no bell of its own. Alerts the original had for other people need `to_roles`; under `hub dev` the user has one role (`hub dev --role "<role>"`, default `Administrador`) |
  | user menu, profile, logout, language, theme | the hub's header (identity from `x-hub-user`); the app shows none of them |
  | company name / logo | the sidebar brand of the original markup, or `AppInfo(name, tagline, icon)` (tile colors `--app-brand`/`--app-on-brand`, §29) |
  | settings, help, "about" links | items of the app's sidebar |

- **Fonts, icon fonts and images:** copy the files into `assets/files/` (served by `asset_file`, see the
  template's `/{id}/assets/files/{name}` route) and keep the `@font-face` rules with relative URLs
  (`url(files/inter.woff2)`): the hub loads the app's own fonts (the family is renamed `app-<name>` for you).
  Fonts from a CDN must be downloaded (external URLs are refused). A logo inlined as `data:image/svg+xml` is
  refused by the sanitizer: save it as `assets/files/logo.svg` and use `<img src="{{ base }}/assets/files/logo.svg">`.
- **Tokens (§29):** declare `--app-primary`/`--app-on-primary`, `--app-brand`/`--app-on-brand` and the
  `--app-chrome*` roles from the original palette (in the overrides) so the hub's parts around the app match it,
  with their dark values. An app that never had a dark theme may stay light: `#app-root{color-scheme:light}`.
- **Themes:** keep the original's theme selectors as they are; the hub's `<html data-theme>` is `light`, `dark`
  or `auto` (follow the system): where the original has a "system" theme, add `:root[data-theme="auto"]` next to
  it (in the overrides) — never add `light`/`dark` to another theme's block.
- **Full-height pages** (a board whose columns scroll inside): the app's content area in the hub is
  `.workspace-content` (already the viewport's height below the hub header, and it scrolls); rules the original
  aims at its own content wrapper (`calc(100vh - header)`) go to `.workspace-content:has(…)`.
- Keep the original `@keyframes` names (the hub prefixes them and their references) and inline SVG.

### 4. Replace the JavaScript, not the behavior

No app JS by default. Each row has a full recipe (original → ported code, the CSS to map, the trap) in
[Adapting recipes](/developers/guias/receitas-adaptacao):

| The app did (JS) | In the hub |
|---|---|
| sidebar collapse, hamburger/drawer | the SDK sidebar has a collapse button on the desktop (`data-hub-menu="collapse"`, remembered per browser) and the drawer on phones (`data-hub-menu="open\|close"`) |
| tabs | `[data-hub-tabs] > [data-hub-tab="#panel"]` |
| modal with a form | keep it a modal: `data-hub-modal="#tpl"` on the button; in `<template id="tpl">` a `<form id="f" data-hub-post="{{ base }}/…" data-hub-target="#f">` with `data-hub-close` on Cancel. Invalid: answer `422` with the form (it replaces itself in the modal). Valid: answer `303` to the page — the modal closes and that page shows |
| "are you sure?" before delete | `data-hub-confirm="Text"` on the form or button, with `data-hub-confirm-title`, `-label` and `-danger` to keep the original wording and color |
| select that filters on change | `<select data-hub-autosubmit>` |
| dropdown, split button ("New ▾"), "⋮" menu | `<button data-hub-dropdown="#menu">` + `<div id="menu" hidden>` with the original markup and CSS |
| collapse/expand kept by the browser (kanban columns, panels; localStorage) | `data-hub-state-class="is-collapsed" data-hub-remember="key"` on the element, `data-hub-class="add\|remove\|toggle"` on the chevron/strip; render the original's default class on the server |
| drag and drop (kanban cards, reordering) with a placeholder | `data-hub-drag="{{ base }}/…"` + `data-hub-drag-id` on the items, `data-hub-dropzone` + `data-hub-drop-name`/`-value` (+ `data-hub-drop-indicator="<original placeholder class>"`) on the lists (§25); map the original's dragging classes to `hub-dragging`/`hub-drop-over` in the overrides; the route answers the move (`before_id`) |
| filter/sort in the table header (funnel per column, spreadsheet style) | `<table data-hub-colfilter>` with `data-hub-col="key"` on each `<th>` (§25): keep the same columns and headers; never swap it for filter fields above the table |
| big list loaded later | `data-hub-get="{{ base }}/…" data-hub-trigger="load"` |
| markdown rendered in the browser | render it on the server |
| dismissible flash message | keep it as it is: the app's own cookies pass through the hub both ways (any name except `hub_*`, no `Domain`), so a cookie flash set on the `303` shows on the next page; a close "×" can be a CSS checkbox (no JS) |
| chart drawn with CSS/JS | keep the CSS bars, or `data-hub-chart` |
| anything else | §25 attributes; only then the reviewed exception of §13.1; otherwise a `- [~]` deviation with the reason |

### 5. Keep Post/Redirect/Get

A valid `POST` answers `303` to the list or the detail (the public base or the app's own path both work): the
hub follows the redirect, shows that page and updates the address bar. Validation errors answer `422` with the
form (use `data-hub-target` to replace only the form). Audit and notifications go in the same transaction as the
change: `with engine.begin() as conn:` … `sdk.outbox.in_transaction(conn, ev)`. Template variables cannot be
named `conn`, `template`, `active` or `status` (parameters of `render.page`).

### 6. Compare with the original — `hub compare`

With `hub dev` and the original running, for every route of the inventory:

```bash
hub compare <original-url> --routes / /tickets /tickets/60 … --login /auth/login --original-selector .layout
```

(`HUB_COMPARE_USER`/`HUB_COMPARE_PASSWORD` for the original's login form; `--original-selector` = the original's
sidebar + content, without its top bar.) Per page it measures, and fails on any of:

- **style** (fonts, palette, radii, counts) above `--max-diff` (15%);
- **structure:** a table column missing or added, a field the original does not have (or lost), a broken image;
- **components** below `--min-components` (90%): each element of the original, paired with the hub's by tag,
  classes and own text, must have the same computed style (color, background, font, size, weight, padding,
  border, radius; sizes within a few pixels). The report lists each one that differs, e.g.
  `h2|lookups: weight 700 → 800` — fix it in the overrides (or the markup) and run again.

`.hub/compare/index.html` shows each page side by side with what differs. Never lower a limit to pass.

### Traps seen in real ports

- Re-implementing `public_base` (or any SDK helper): links lose `/staging` and staging pages land in production.
- Editing the original CSS "to fit": every change goes to the overrides (`hub check` fails otherwise).
- Adding `light`/`dark` to the original's "system" theme block: the light theme's colors are lost.
- A board or a full-height page growing to the tallest column: map its height rule to `.workspace-content:has(…)`.
- Filter fields above a table whose original had filters in the header: use `data-hub-colfilter`.
- Invented sample data: compare against the original's own records.
- A class of the original with the same name as one of the hub kit's (`data-table`, `card`, `btn`…): the kit's
  rules apply too; `hub compare` shows the differing properties — neutralize them in the overrides.
- Dropping small things that need JS (a "+" in a column header, a caret, a kebab): they are in the inventory and
  have a kit equivalent.
- Comparing only the first page: every route of the inventory goes through `hub compare`.

### Done

- `hub check` green: `port-inventory.md` with no `- [ ]`, the original CSS untouched.
- `hub compare` passes on every route of the inventory.
- Every behavior tried on both sides; dark theme and phone layout checked.
- Published to staging; the final report has: the routes with their `hub compare` numbers, every `- [~]` deviation
  with its reason, and what you could not verify.

A full example, with the code that changed: [Adapting an existing app: the Frota+ example](/developers/guias/adaptar-app).

## Rules

- Never commit secrets (`.env`, tokens, keys); never write the token to project files.
- Production (`--env production`) only with approval in the hub — publish to staging and request the approval.
