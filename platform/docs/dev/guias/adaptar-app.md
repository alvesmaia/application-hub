# Adapting an existing app: the Frota+ example

A worked example of the rules in [Instructions for agents › Adapting an existing app](/developers/agente).
Frota+ is a fleet-management app (FastAPI + Jinja2) built outside the hub. It has its own top bar
(global search, a bell with 7 notifications, a user menu), a sidebar with SVG icons and a "Recolher"
(collapse) button, CRUD forms with flash messages, and reports. The port kept every page, URL, icon and
color, and moved what the top bar did into the hub.

## What went where

| Frota+ (as is) | In the hub (to be) |
|---|---|
| logo + "Frota+" in the top bar | sidebar brand: `AppInfo(name="Frota+", tagline="Gestão de frota", icon=LOGO)`; the tile color comes from `--app-brand`/`--app-on-brand` in `app.css` |
| global search box | the hub has no global search: `/busca` stays as an app page, reached from a sidebar item |
| bell + dropdown, own notifications table | Inbox notifications: `outbox.notification(..., to_roles=["Gestor de frota"])` written in the action's transaction; the table and `/notificacoes/*` are gone |
| user menu (Perfil, Preferências, Sair) | the hub's header. `/sair` is dropped; `/perfil` shows the hub's identity (`identity_of(req)`) |
| sidebar with 6 SVG icons | the same labels, order and SVG bodies: `NavItem("veiculos", "Veículos", ICONS["veiculos"])` (text, no `Markup`); `help_card=False` |
| "Recolher" button (JS) | the hub's sidebar collapse button (desktop) and drawer (phones); no app JS |
| modal forms, "are you sure?" | `data-hub-modal`, `data-hub-confirm`, `data-hub-confirm-danger` |
| flash message with a close "×" | the app's own cookie flash (it crosses the hub); the "×" is a CSS checkbox, no JS |
| own CSS (teal, rounded) | `assets/app.css`: the palette as `--app-*` tokens (light and dark), rules with `var(--…)` |

## The code that changed

The sidebar and the brand (`app.py`). Icons are the original SVG bodies, as plain strings:

```python
ICONS = {
    "veiculos": '<path d="M3 7h11v9H3z"/><path d="M14 10h4l3 3v3h-7z"/><circle cx="7" cy="18" r="2"/>'
    '<circle cx="17" cy="18" r="2"/>',
    # … one entry per item of the original sidebar
}
INFO = AppInfo(
    id="frota",
    name="Frota+",
    tagline="Gestão de frota",
    icon='<g transform="scale(.75)" fill="currentColor" stroke="none"><path d="M7 11h11v9H7z"/></g>',
    nav=tuple(NavItem(section, label, ICONS[key]) for section, label, key in NAV),
    help_card=False,
)
```

The brand tile and the palette (`assets/app.css`, literal colors only in the token declarations):

```css
:root { --app-brand: #0f766e; --app-on-brand: #ffffff; --app-primary: #0f766e; /* … */ }
:root[data-theme="dark"] { --app-brand: #14b8a6; --app-on-brand: #042f2e; /* … */ }
@media (prefers-color-scheme: dark) { :root[data-theme="auto"] { --app-brand: #14b8a6; --app-on-brand: #042f2e; } }
```

The bell, as an Inbox notification that commits with the save (`with engine.begin() as conn`):

```python
with engine.begin() as conn:
    vehicle_id = store.save_vehicle(conn, form)
    if form.maintenance_overdue:
        sdk.outbox.in_transaction(conn, outbox.notification(
            who, f"Manutenção vencida: {form.plate}", link=f"/frota/veiculos/{vehicle_id}/editar",
            to_roles=["Gestor de frota"], level="warning"))
return RedirectResponse(f"{public_base(req)}/veiculos", status_code=303)
```

Under `hub dev` the simulated user has one role (`--role`, default `Administrador`): to see a notification
sent `to_roles=["Gestor de frota"]`, run `hub dev --role "Gestor de frota"`.

## Pitfalls found in this port

- `render.page(req, template, active, status, **ctx)`: `conn`, `template`, `active` and `status` are
  parameters, so they cannot be template variables (rename a `status` filter to `status_filter`).
- `@keyframes` are renamed `app-<name>` inside the hub and the `animation` references follow; write the
  original names. Inline SVG is kept (not `<animate>`/`<set>`/`<foreignObject>`).
- The local preview shows a loader for about a second on the first load: in automated checks, wait for
  the page header before clicking.
- Check `hub --version`: the `hub X` part must match the hub's version (`/healthz`).
