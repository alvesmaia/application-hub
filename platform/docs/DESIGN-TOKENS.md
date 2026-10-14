# Design tokens — Alvesmaia identity

Single source of the platform's look: **`assets/shell/tokens.css`**, served at
`/assets/tokens.css` by hub-web and `hub dev` and loaded **before** `styles.css`.
App fragments are rendered inside the same shell document,
so they use the same variables without importing anything.

```
tokens.css   → primitives + semantics + scales (space, radius, type, shadow, layers, motion)
theme.css    → installation theme (white label): redefines only semantics (today --color-accent)
styles.css   → shell components (mockup), only with var(--…)
hub.css / ops.css / auth.css / demo.css / app.css → same
```

## Alvesmaia identity (colors, fonts, lines — no logo)

Single source of the colors: **`scripts/brand-palette.json`**. Each **role** has a light and a
dark (graphite) value chosen by hand; nothing is computed. `node scripts/gen-dark-theme.mjs`
writes to `tokens.css` the `paleta:inicio/fim` block (`--am-<role>` light + primitives) and the
`tema-escuro:inicio/fim` block (only the dark `--am-<role>`); `--check` (in the JS tests and in
`node scripts/gen-dark-theme.mjs --check`) fails if something is out of date **or** if any pair declared in
`contrast` falls below 4.5:1 (text) / 3:1 (shape, field border) in any theme.

| Role | Light | Dark | Use |
|---|---|---|---|
| `page` | `#F4F6FB` | `#191919` | page background |
| `surface` / `raised` | `#FFFFFF` | `#202020` / `#272727` | cards, tables, fields / popover, menu, modal |
| `alt` / `hover` | `#F6F8FD` / `#F0F3F8` | `#1E1E1E` / `#252525` | list footer, zebra / row on hover and row divider |
| `border` / `border-strong` | `#E1E6F0` / `#C9D2E3` | `#2F2F2F` / `#3D3D3D` | card border / strong decorative, scrollbar |
| `control` | `#7B8799` | `#6B6B6B` | field border (≥ 3:1) |
| `tint` / `tint-border` | `#EEF1FF` / `#C5D0FF` | `#232733` / `#3A4266` | chip, highlight / secondary button border |
| `title` / `body` | `#102A43` / `#334E68` | `#FFFFFF` / `#EDEDED` | titles / running text |
| `secondary` / `faint` / `ink` | `#52606D` / `#5F6B7A` / `#0F1E2B` | `#A5A5A5` / `#9A9A9A` / `#FFFFFF` | labels / subdued / near black |
| `primary` / `primary-hover` / `on-primary` | `#3A4FD8` / `#2E3FB8` / `#FFFFFF` | `#8FA3FF` / `#AEBCFF` / `#08122E` | primary button, links, highlighted text |
| `fill` | `#3A4FD8` | `#4A64F5` | indigo fill with white text in both themes (apps) |
| `accent` / `accent-soft` | `#4F6BFF` / `#8FA3FF` | `#4F6BFF` / `#6F86FF` | shapes, charts, focus — **never running text** |
| `ok`, `ok-bg` | `#0B6B3A`, `#DDF5E8` | `#4FC79A`, `#1A2E25` | success (also `ok-border`, `ok-shape`) |
| `warn`, `warn-bg` | `#8A4B00`, `#FFF1D6` | `#E0A661`, `#2E2517` | warning (also `warn-border`, `warn-shape`) |
| `err`, `err-bg` | `#9B1C1C`, `#FDECEC` | `#F08B7C`, `#33201E` | error (also `err-border`, `err-shape`, `err-fill`) |
| `neutral-bg` | `#EEF0F4` | `#272727` | neutral badge (text `secondary`) |
| `chrome` / `chrome-raised` | `#102A43` / `#17365A` | `#1E1E1E` / `#272727` | header + rail / tooltip, toast |
| `chrome-text` / `chrome-text-2` / `chrome-icon` | `#FFFFFF` / `#B8C4D6` / `#C5D0FF` | `#FFFFFF` / `#A5A5A5` / `#C5D0FF` | text, secondary and icons over the chrome |

Active item in the rail/sidebar: background `rgba(79,107,255,.28)`, white text and
`box-shadow: inset 3px 0 0 #4F6BFF` (`--color-chrome-active-bg`, `--color-chrome-active-bar`).
Focus: `outline: 2px solid` `--color-focus` (`#4F6BFF`), 2px offset (-2px inside
tables, popovers and modals).

**Fonts** (the same as the Alvesmaia site), served by the hub itself at `/assets/fonts/`
(variable woff2, `font-display: swap`; SIL OFL 1.1 license at `/assets/fonts/OFL-NunitoSans.txt`
and `/assets/fonts/OFL.txt`). **Nunito Sans** (axis 400–900, latin subset — covers the accents
of pt-BR; `format("woff2-variations")`) across the whole interface: `--font-body`, `--font-ui` and
`--font-display` (titles, KPIs). **Red Hat Display** only in the application name in the header
(`--font-brand`, `.brand-name`). `--font-mono` **Red Hat Mono** (keys, values, IDs, code).
The shell preloads Nunito Sans and Red Hat Display; CSP `font-src 'self'`; fallbacks
`system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif` and
`ui-monospace, Consolas, monospace`.

**Sizes** (real px — the `html` has no `zoom`): body 13px/400, `letter-spacing: -.005em`;
h1–h6 800, `letter-spacing: -.02em` — h1 26px (22px in the app header, 24px on short screens),
h2 16px, card title 15px; KPI 27px/800 `tabular-nums`; buttons 13px/500, `line-height 1.4`,
`padding 6px 14px`, height 32px (12px inside tables); tabs/pills 13px/500 (28px); badges
12px/600; table header 11px/600 UPPERCASE `letter-spacing: .05em` `secondary`; cell
13px, `padding 8px 12px`; navigation items 500 (active 600), 32px high and 2px between items;
eyebrow 12px/700, `letter-spacing: .12em`, primary color. Floor: text ≥ 12px (except the table header),
clickable targets ≥ 28px. Header 41px, expanded rail 204px, brand icon 34px,
search centered across the full width (max. 440px).

**Lines and shapes**: borders always 1px; card with a `border` border, radius 12, **no shadow**;
table rows separated by a 1px `hover` line (`#F0F3F8`), header by a 1px `border`; buttons,
fields and selects radius 8 (36–40px high); clickable rows and menu items radius 6; pills
and tabs radius 16 (30–32px); badges radius 11. Shadow only on floating elements (tooltip, toast,
menu, modal): `0 8px 24px rgba(16,42,67,.16)` (dark `0 18px 44px rgba(0,0,0,.44)`).
Solid primary button (`primary`, text `on-primary` 700); secondary button white with a
`tint-border` border and `primary` text; icon button 32–40px square, radius 8. Dense tables
(row ~38–40px, 14px, numbers right-aligned with `tabular-nums`), thin scrollbars (6px,
thumb `border-strong`). The rules sit at the end of `hub.css` ("Identidade Alvesmaia" (Alvesmaia identity)), on top
of the mockup's inherited layers.

## Layers

0. **Roles**: `--am-<role>` (table above), generated from `scripts/brand-palette.json`.
1. **Color primitives**: `--c-<family>-<lightness>`, names inherited from the mockup that the old CSS
   still uses; today each one is only an alias of a role (`--c-blue-31: var(--am-primary)`)
   or a fixed value in both themes (`{"fixed": "#hex"}`, e.g. the log terminal, always dark).
   The mapping is in `primitives` of the JSON.
2. **Semantics**: what new components should use:

| Token | Use |
|---|---|
| `--color-bg` | page background |
| `--color-surface`, `--color-surface-raised`, `--color-surface-muted`, `--color-surface-sunken`, `--color-surface-hover` | cards, floating elements, alternating bands, hover |
| `--color-text`, `--color-text-strong`, `--color-text-ink`, `--color-text-secondary`, `--color-text-muted`, `--color-text-subtle` | texts by emphasis |
| `--color-text-inverse`, `--color-border-inverse` | white text/border (over the chrome and fills) |
| `--color-border`, `--color-border-strong`/`--color-border-control`, `--color-divider` | borders, field border (3:1), dividers |
| `--color-accent`, `--color-accent-hover`, `--color-on-accent` | primary (links, buttons, highlighted text), **white label** |
| `--color-accent-shape`, `--color-accent-shape-2`, `--color-focus` | shapes, charts and focus (`#4F6BFF`) |
| `--color-tint`, `--color-tint-border`, `--color-accent-soft` | chips and highlights |
| `--color-brand` | title color (navy in light, white in dark) |
| `--color-chrome*` | header and rail (background, text, icons, active item) |
| `--color-success/-warning/-danger` (+ `-soft`, `-shape`), `--color-danger-strong`, `--color-info(-soft)`, `--color-neutral-soft` | states |
| `--focus-ring`, `--focus-ring-color` | focus ring |
| `--app-color`, `--app-ink` | identity of the open app (`hub.js` publishes it from the catalog) |

3. **Scales**

| Group | Tokens |
|---|---|
| Space | `--space-2 … --space-48` (name = px; closed scale 2,4,6,8,10,12,14,16,18,20,24,28,32,40,44,48) |
| Radius | `--radius-2xs` (2) `xs` (4) `sm` (5) `md` (6) `lg` (7) `xl` (8) `2xl` (9) `3xl` (10) `4xl` (11) `5xl` (12) `6xl` (14) `7xl` (20) `pill` `round` |
| Typography | `--font-body`/`--font-ui`/`--font-display` (Nunito Sans), `--font-brand` (Red Hat Display, brand only), `--font-mono`/`--font-mono-alt` (Red Hat Mono); `--font-size-4xs`…`--font-size-sm` (12px, floor) … `--font-size-base` (14px) … `--font-size-7xl` (27px) |
| Shadows | `--shadow-card` (`none`), `--shadow-popover`, `--shadow-modal` (floating only) |
| Layers | `--z-raised` 1 · `--z-rail` 25 · `--z-header` 30 · `--z-rail-restore` 35 · `--z-popover` 40 · `--z-overlay` 45 · `--z-backdrop` 49 · `--z-sidebar` 50 · `--z-tooltip` 60 · `--z-toast` 70 · `--z-menu` 80 |
| Motion | `--ease-emphasized`, `--duration-fast`, `--duration-base` (zeroed with `prefers-reduced-motion`) |

## White label

Plataforma › Configurações › **Brand color** («Cor da marca»): saves `brand_color` (`#rrggbb`, validated on the
server — nothing but hex reaches the CSS) and `/assets/theme.css` now has
`:root { --color-accent: <cor>; }`. Everything that uses the accent (shell primary buttons, links,
focus, eyebrows, highlights of apps that follow the tokens) changes immediately, with no rebuild.

## Dark theme (graphite)

The dark values are **explicit** (the "Escuro" (Dark) column above, in `scripts/brand-palette.json`);
the generator only writes them to the `tema-escuro:inicio/fim` block of `tokens.css` (do not edit by hand).
It applies to `:root[data-theme="dark"]` (the user's choice, saved on the server and posted to the
`<html>` by the shell) and, when the user chooses "Automático (sistema)" (Automatic (system)), to
`prefers-color-scheme: dark` (`:root[data-theme="auto"]`). **The default is the light theme**: whoever
never chose (missing preference or the old empty value) gets `data-theme="light"` from the
server, even with a dark OS — no flash; `<meta name="color-scheme">` follows
(`light` / `dark` / `light dark` in automatic). Login/credential screens are always light. Since primitives and semantics point to `--am-*` through
`var()`, the dark block redefines only the roles.

- **Chrome**: navy in light, graphite (`#1E1E1E`) in dark, with the same indigo bar on the active item. There is
  no longer a special rule "chrome keeps light".
- **Mockup apps**: `app2.css`/`app3.css` have no literal colors: the generator replaced each one with
  a hub token (`var(--am-<role>)`, `color-mix` where there was transparency), chosen by the
  property (text/border/background) and by the color; a new literal color in those files is converted when
  the generator runs and rejected by `--check`. These apps' own terracotta/forest palette was removed
  (it did not pass contrast in both themes); each app's identity stays in `--app-ink`/
  `--app-color` (catalog; `fornecedores`/`contratos` inks darkened to 4.5:1).
- **Brand color** (white label): in dark, `theme.css` mixes the brand color with
  white (45%), to keep the contrast.
- **Charts**: `hub-chart.js` uses the identity's series palette (`#4F6BFF` first).
- **Contrast**: `--check` guarantees the declared pairs; the probe `tests-e2e/a11y-probe.mjs`
  (axe, both themes) measures the real pages. The old `scripts/fix-contrast.mjs` (automatic adjustment on a
  computed theme) was retired: adjust the role in the JSON and run the generator.

## For app authors

- Use the semantics (`var(--color-text-muted)`, `var(--space-12)`, `var(--radius-xl)`…) and
  `var(--app-ink)`/`var(--app-color)` for your app's identity.
- The Python SDK (`templates/*.html`) and the app fragments use only shell classes and tokens;
  each app's own CSS (`demo.css`, `app.css`, `app2.css`, `app3.css`) does too.

## Mobile and responsive tables (hub)

CSS only for the hub (`hub.css`; not an app contract — no new `data-hub-*`):

- **Table → cards** (`.hub-table-cards` on the `<table>`, `.hub-cards-panel` on the panel).
  ≥ 1024px normal table; 768–1023px `.hub-col-secondary` disappears; < 768px each `<tr>` becomes a
  card: `td.hub-card-title` (`.hub-avatar` + name + `.hub-truncate` with `title`) and
  `td.hub-card-actions` (menu "⋯", target ≥ 40px) in the 1st row; other cells with a label from
  `data-label`. `.hub-hide-cards` disappears and `.hub-cards-only` appears in the cards (e.g.
  "Editar" (Edit) inside the menu). Use explicit `role="table|rowgroup|row|columnheader|cell"`
  (the `display` changes and the browser would lose the semantics). Applied in Acesso › Usuários (Access › Users).
- **Column filters** (`<table data-hub-colfilter data-hub-colfilter-empty="…">`, only in the hub
  screens — `hub-ux.js`; not exposed to apps): each `<th data-hub-col="chave"
  data-hub-col-label="Rótulo" [data-hub-col-type="list|date"]>` gets a funnel (filled when
  active) that opens a popover with sort A→Z/Z→A, a searchable list of values (> 8 values) and
  "Selecionar todos" (Select all) — or a From/To range for dates — and Limpar (Clear)/Aplicar (Apply). Cell value:
  `data-hub-value` (dates in ms) or the text. State in the URL (`f.<chave>=a,b`,
  `f.<chave>=AAAA-MM-DD..AAAA-MM-DD`, `sort=<chave>:asc|desc`) — the link is shareable.
  "Filtros ativos" (Active filters) line: … ✕ · "Limpar tudo" (Clear all) above the table. < 768px the popover becomes a bottom sheet
  and the "Filtros" (Filters) button lists the columns (suits the card layout, which has no
  header). Applied in Operação › Tarefas (Operations › Tasks).
- **Button with menu** (`details.hub-row-menu.hub-new-menu` with `summary.primary-button`):
  a main action with variations ("Nova conexão ▾" (New connection)); items `role="menuitem"`, arrows ↑/↓.
- **"⋯" menu** (`details.hub-row-menu`): `hub-ux.js` opens upward
  (`.hub-row-menu--up`) or aligns to the left (`--start`) when it would leave the screen.
- **Sheet** (`.modal.hub-sheet`): < 768px the modal fills the screen, actions fixed in the footer,
  fields ≥ 44px, one column.
- **Shell < 768px**: no rail; brand on the left and, on the right, search (icon; ⌘K palette full screen),
  notifications and ☰ (`[data-hub-drawer-toggle]`; avatar goes to the menu). The ☰
  opens the `#hub-rail` as a full-width panel from top to bottom under the header
  (`body.hub-drawer-open`; user at the top and account/theme/language/Sair (Sign out) at the end — blocks
  `.hub-drawer-only`) and becomes ✕; focus trapped, Esc/background/✕/tapping an item closes it, page scrolling
  locked. In the app, the fixed bar "☰ App · Seção" (Section) (`[data-hub-menu="open"]`, label set
  by hub.js) opens the app's sections as a panel right below it. E2E
  `tests/22-mobile.spec.ts`; sweep with units `admin-*-390`.

## Visual regression

`tests-e2e/visual/visual.spec.ts` + `playwright.visual.config.ts`: 12 screens (login, home,
catalog, Administration, Operations, `ativos`, `servicos`) compared with the reference recorded
**before** the extraction (1% pixel tolerance; relative times and the rail — which gained
new items in wave 8 — masked). Run with the native hub (`bash scripts/demo.sh` on
fresh data):

```bash
cd platform/tests-e2e
npx playwright test -c playwright.visual.config.ts            # compares
npx playwright test -c playwright.visual.config.ts --update-snapshots  # new reference
```

Wave 9: references re-recorded (new Início (Home), header with search/Inbox, grays darkened for contrast) and 12 new screens in the dark theme — 24/24 stable across two runs.

Alvesmaia identity: references deliberately re-recorded (theme change: colors, Red Hat fonts and lines).

Wave 8 result: 11/12 screens identical to the reference; the Settings one differs only by the
new fields (brand color, public status and vault panel). References are per
platform (`-win32`), so the suite does not run in the Linux CI.
