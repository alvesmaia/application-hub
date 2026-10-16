# Getting started

Create a platform app, run it on your computer and publish it to this hub. Prerequisites: **uv** and Python 3.12 —
no Docker, no Postgres, no access to the repository.

> **Coding agents:** read [`/dev/llms.txt`](@HUB@/dev/llms.txt). It lists the original Markdown of each page
> (`/dev/docs/<página>.md`), with the complete code. A summary of the HTML loses the examples.

## Install the CLI

```bash
uv tool install hub-cli --index @HUB@/dev/cli/simple/
hub --help
```

The index `@HUB@/dev/cli/simple/` is public and read-only (wheels of `hub-cli` and `hub-sdk`). Already installed?
Update with `uv tool install --reinstall hub-cli --index @HUB@/dev/cli/simple/`. Today there is one stack:
`python-fastapi` (`hub project stacks`).

## First app in 5 commands

```bash
hub project create estoque --stack python-fastapi --yes
cd estoque && uv sync
hub check
hub dev
hub deploy --env staging --yes
```

1. `hub project create` generates the app from the template (manifest, screens, API, tests, `AGENTS.md`).
2. `uv sync` installs the dependencies (the SDK comes in `vendor/`).
3. `hub check` runs lint, types, tests with coverage, manifest, `openapi.json` and local conformance.
4. `hub dev` starts a local hub with the app inside the shell at `http://localhost:4173/apps/estoque`.
5. `hub deploy` packages and sends it; production (`--env production`) stays pending approval. No token? `hub pack --build-dir .`
   and import the `.hubapp` on the Deployments screen ([Publishing](/developers/guias/publicar)).

## Token (only to publish)

`project create`, `check` and `dev` work without login. To publish, [create a token](@HUB@/admin/tokens) with the **envio** (send) and **status** actions under Credenciais (Credentials) and save it in the CLI (the token is
requested without echo and stays outside the project):

```bash
hub login --hub @HUB@
```

In CI, use `--token-file` or the `HUB_TOKEN` variable; never write the token to project files.

## Rules that `hub check` enforces

- **Mandatory API** (Contract §23): keep `install_api` in `app.py` and `"api": {"enabled": true}` in the manifest,
  even if the app only has screens. Changed a route? `hub check` regenerates `openapi.json`; only `pytest` fails with the old one.
- **Formatting**: `uv run ruff format . && uv run ruff check --fix .` fixes what `hub check` reports.
- **No app JavaScript of your own**: screen behavior comes from `data-hub-*` attributes
  ([Screen patterns](/developers/guias/telas)); real-time in [Real-time](/developers/guias/tempo-real).
- `hub dev` reloads when you change `src/`; changed `pyproject.toml` or `manifest.json`? Run `uv sync` and start `hub dev` again.

## Next steps

- [CLI reference](/developers/cli) and [SDK reference](/developers/sdk)
- [Guides](/developers/guias): first app, publishing, database, storage, screens, API and MCP
- [App contract](/developers/contrato): what the hub requires of every app
