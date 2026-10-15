# Instructions for agents

You are a coding agent. Follow these steps to create and publish an app on this Application Hub
(`@HUB@`). If the request does not say what the app does, ask before starting.

## 1. Install the CLI

Prerequisite: **uv** and Python 3.12. The CLI comes from this hub's public index:

```bash
uv tool install hub-cli --index @HUB@/dev/cli/simple/
hub --help
```

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

The token comes from the hub owner; the CLI asks for the token without echo and stores it outside the project:

```bash
hub login --hub @HUB@
hub deploy --env staging --yes
```

Without access to the hub, generate the package and deliver the `.hubapp` file:

```bash
hub pack --build-dir .
```

## Rules

- Never commit secrets (`.env`, tokens, keys); never write the token to project files.
- Production (`--env production`) only with approval in the hub — publish to staging and request the approval.
