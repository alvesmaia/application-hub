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

The person expects to open the hub and find **their app as it was**. Rewriting it in the template's look is a
failure even when `hub check` is green.

1. **Inventory before touching code.** Run the original app and write down, as a checklist you will verify at the
   end: every route (including nested ones like `/veiculos/12/editar`), every link of the sidebar and its icon,
   every form (fields, validation messages, what happens after saving or deleting), every JavaScript behavior,
   the flash messages, and the visual identity (colors, fonts, radii, shadows, the sidebar look). Take screenshots
   (desktop, phone). Then create the project (step 2) next to the original and port it page by page.
2. **Keep the URLs.** Each route keeps its path under `/{id}/…`; nested paths work (deep links open the same page).
   Build links with `{{ base }}` (the public base); never flatten routes to `?id=`.
3. **Return fragments, keep the layout.** Drop the app's own `<html>`/`<head>`/`<body>` and top bar: the page is
   the `workspace` fragment (§3 of the [contract](@HUB@/dev/docs/contrato.md)). The app's sidebar becomes the
   workspace sidebar: same labels, same order, same icons — pass the app's own SVG body as a plain string,
   `NavItem("veiculos", "Veículos", '<path …/>')` (no `Markup`), or a name from `hub_sdk.render.ICONS`; the
   same for the logo in `AppInfo(icon=…)`. `AppInfo(help_card=False)` keeps the sidebar without the hub's
   help card. What the top bar showed goes where the hub already has it — never a
   second header inside the app:

   | The app's top bar had | In the hub |
   |---|---|
   | search box (global) | the hub has no global search: keep the app's search as a page of its own (a sidebar item) or, inside a list, as its filter bar (`data-hub-filter` or a `GET` form) |
   | notifications bell, unread counter | the hub's Inbox: `hub_sdk.outbox.notification(user, title, link=…, to_roles=[…] or to_users=[…])` (§20; empty = only the user who caused it), written with `sdk.outbox.in_transaction(conn, ev)` in the action's transaction; no bell of its own. Alerts the original had for other people need `to_roles`; under `hub dev` the user has one role (`hub dev --role "<role>"`, default `Administrador`) |
   | user menu, profile, logout, language, theme | the hub's header (identity from `x-hub-user`); the app shows none of them |
   | company name / logo | the sidebar brand (`AppInfo(name, tagline, icon)`; tile colors `--app-brand`/`--app-on-brand`, §29) or the page header (`h1` + subtitle) |
   | settings, help, "about" links | items of the app's sidebar |
4. **Keep the identity.** Move the CSS to `/{id}/assets/app.css` and translate the app's palette into the
   `--app-*` tokens (§29), **including** `--app-chrome`, `--app-chrome-raised`, `--app-chrome-text`,
   `--app-chrome-text-2` and `--app-chrome-icon` (the sidebar), `--app-brand`/`--app-on-brand` (the logo tile),
   plus `--app-primary`/`--app-on-primary`. Hex values go **only** in these token declarations; every other
   rule uses `var(--…)` ("colors only through tokens" means this). Write the dark theme too (mandatory): the
   same roles under `:root[data-theme="dark"]` and the `auto` media query. Keep the original `@keyframes`
   names (the hub prefixes them and their references) and inline SVG.
5. **Replace the JavaScript, not the behavior** (no app JS by default):

   | The app did (JS) | In the hub |
   |---|---|
   | sidebar collapse, hamburger/drawer | the SDK sidebar has a collapse button on the desktop (`data-hub-menu="collapse"`, remembered per browser) and the drawer on phones (`data-hub-menu="open\|close"`) |
   | tabs | `[data-hub-tabs] > [data-hub-tab="#panel"]` |
   | modal with a form | keep it a modal: `data-hub-modal="#tpl"` on the button; in `<template id="tpl">` a `<form id="f" data-hub-post="{{ base }}/…" data-hub-target="#f">` with `data-hub-close` on Cancel. Invalid: answer `422` with the form (it replaces itself in the modal). Valid: answer `303` to the page — the modal closes and that page shows |
   | "are you sure?" before delete | `data-hub-confirm="Text"` on the form or button, with `data-hub-confirm-title`, `-label` and `-danger` to keep the original wording and color |
   | select that filters on change | `<select data-hub-autosubmit>` |
   | dismissible flash message | keep it as it is: the app's own cookies pass through the hub both ways (any name except `hub_*`, no `Domain`), so a cookie flash set on the `303` shows on the next page; a close "×" can be a CSS checkbox (no JS) |
   | chart drawn with CSS/JS | keep the CSS bars, or `data-hub-chart` |
   | anything else | §25 attributes; only then the reviewed exception of §13.1 |

6. **Keep Post/Redirect/Get.** A valid `POST` answers `303` to the list or the detail (as the template does):
   the hub follows the redirect, shows that page and updates the address bar. Validation errors answer `422`
   with the form (use `data-hub-target` to replace only the form). Audit and notifications go in the same
   transaction as the change: `with engine.begin() as conn:` … `sdk.outbox.in_transaction(conn, ev)`. Template
   variables cannot be named `conn`, `template`, `active` or `status` (parameters of `render.page`).
7. **Verify against the checklist** in `hub dev`: every page, every sidebar link, every form (valid and invalid),
   delete, the phone layout and the dark theme, side by side with your screenshots of the original. Anything you
   could not keep goes in your final report, with the reason. A full example, with the code that changed:
   [Adapting an existing app: the Frota+ example](/developers/guias/adaptar-app).

## Rules

- Never commit secrets (`.env`, tokens, keys); never write the token to project files.
- Production (`--env production`) only with approval in the hub — publish to staging and request the approval.
