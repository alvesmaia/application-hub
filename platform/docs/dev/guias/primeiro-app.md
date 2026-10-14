# First app

## Create the project

```bash
hub project create estoque --name "Estoque" --stack python-fastapi --requires database --yes --json
```

Without `--yes` and with a terminal, the CLI asks for whatever is missing. An invalid parameter returns exit code `2`.

## What the template includes

| Path | Contents |
|---|---|
| `manifest.json` | id, name, version, routes, `requires` (what the hub provisions) |
| `src/<pacote>/app.py` | FastAPI + `HubSdk` (readiness, identity, public base) |
| `src/<pacote>/templates/` | Jinja2 screens that extend the SDK workspace |
| `src/<pacote>/migrations/` | Alembic migrations (SQLite in dev, Postgres in the hub) |
| `openapi.json` | not in the template: `hub check` generates it from the API and regenerates it on every change |
| `tests/` | pytest with an 80% coverage floor |
| `AGENTS.md` | rules for coding agents and people |

## Development cycle

```bash
hub dev
hub check
hub check --only ruff,pytest --json
```

## Test by hand (curl)

Screens and actions go through the hub at `/_frag/<id>/…` (the browser sees `/apps/<id>/…`). With `hub dev`
running, the simulated user is already logged in:

```bash
curl http://localhost:4173/_frag/estoque/itens
curl -X POST http://localhost:4173/_frag/estoque/itens      -H "Origin: http://localhost:4173"      -H "Idempotency-Key: $(date +%s%N)"      --data-urlencode "name=Parafuso" --data "quantity=7"
```

- `Origin` equal to the hub address is **required** on POST, PUT, PATCH and DELETE. Without it, the hub responds
  403 ("Requisição bloqueada" (Request blocked)), the cross-site submission protection.
- `Idempotency-Key` is optional and must be 8 to 128 characters long (otherwise: 400). The browser sends a
  new key on each submission. Repeating the same key returns the stored response **without running again**.
- POST to `/apps/<id>/…` (the page address) does not run the action (404/405). Actions always go to `/_frag/<id>/…`.
- The JSON API (`/apps/<id>/api/v1/…`) requires a token: `-H "Authorization: Bearer <token>"`.

## Rules the hub checks

- Links and `action` use the public base (`{{ base }}`), never a raw `/<id>/…`.
- No app JavaScript of your own: behaviors through `data-hub-*` attributes ([Screen patterns](/developers/guias/telas)).
- Colors only through hub tokens (`var(--…)`).
- `GET /<id>/_hub/ready` responds (the `HubSdk` installs it). It is an internal route, called only by the hub: through the
  browser or `/_frag`, the gateway returns 404 for `/_hub`.
