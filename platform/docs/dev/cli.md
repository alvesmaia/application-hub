<!-- gerado por `uv run python -m hub.ops.devdocs` — não edite à mão -->

# Referência da CLI

Gerada do `--help` da CLI `hub` (argparse). Rode `hub <comando> --help` para a mesma informação no terminal.

## Códigos de saída

| Código | Significado |
|---|---|
| `0` | ok |
| `1` | falha (checagem ou etapa) |
| `2` | validação (parâmetro ausente ou inválido) |
| `3` | autenticação |
| `4` | hub inacessível |

## `hub project create`

New app (interactive in a terminal; non-interactive with --yes or without a TTY).

```
hub project create [-h] [--name NAME] [--stack STACK] [--db DB] [--requires REQUIRES] [--dir DIR] [--template {basic,dashboard}] [--with WITH_] [--yes] [--json] [id]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `id` | app id (2–32: a-z, 0-9, '-') |  |
| `--name` | display name (default: capitalized id) |  |
| `--stack` | stack (see `hub project stacks`; default python-fastapi) |  |
| `--db` | sqlite (default) or database URL (written to .env DATABASE_URL) |  |
| `--requires` | what the hub provisions: database,storage (default: database) |  |
| `--dir` | target folder (default: ./<id>) |  |
| `--template` (basic \| dashboard) | basic (default) or dashboard (KPIs, charts, table, filter and export) |  |
| `--with` | extras: dashboards (= --template dashboard) |  |
| `--yes`, `-y` | no questions: use the defaults |  |
| `--json` | JSON output |  |

## `hub project stacks`

List the available stacks.

```
hub project stacks [-h] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--json` |  |  |

## `hub check`

ruff, ty, pytest+coverage, manifest, openapi and local conformance.

```
hub check [-h] [--dir DIR] [--only ONLY] [--full] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--only` | comma-separated steps: manifesto,ruff,formato,ty,openapi,pytest,conformidade |  |
| `--full` | also the full contract suite (conformance.json) |  |
| `--json` |  |  |

## `hub pack`

Build the .hubapp package from the app folder.

```
hub pack [-h] [--dir DIR] [--image IMAGE] [--build-dir BUILD_DIR] [--dockerfile DOCKERFILE] [--out OUT]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--image` | image by digest (name@sha256:…); alternative: --build-dir |  |
| `--build-dir` | build context: the hub builds the package image |  |
| `--dockerfile` | Dockerfile inside --build-dir | `Dockerfile` |
| `--out` |  |  |

## `hub dev`

Lightweight local hub (no Docker) with the app inside the shell.

```
hub dev [-h] [--dir DIR] [--upstream UPSTREAM] [--port PORT] [--role ROLE] [--limits] [command ...]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--upstream` | app already running (otherwise the CLI starts the project app) |  |
| `--port` |  | `4173` |
| `--role` |  | `admin` |
| `--limits` |  |  |
| `command` |  |  |

## `hub login`

Save the hub and the token (the token is prompted without echo).

```
hub login [-h] --hub HUB [--token TOKEN] [--token-file TOKEN_FILE] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--hub` |  (obrigatório) |  |
| `--token` | AVOID: use HUB_TOKEN, --token-file or the no-echo prompt |  |
| `--token-file` | file with the token (e.g. provided by the CI vault) |  |
| `--json` |  |  |

## `hub deploy`

Package and send to the hub (production stays pending approval).

```
hub deploy [-h] --env {staging,production} [--dir DIR] [--hub HUB] [--token TOKEN] [--env-file ENV_FILE] [--yes] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--env` (staging \| production) |  (obrigatório) |  |
| `--dir` |  | `.` |
| `--hub` | hub URL (default: the one from `hub login` or HUB_URL) |  |
| `--token` | AVOID: use `hub login` or HUB_TOKEN |  |
| `--env-file` | values for requires (default: deploy.<env>.env) |  |
| `--yes`, `-y` | no questions |  |
| `--json` |  |  |

## `hub mcp`

stdio MCP server that forwards to the hub MCP with a token.

```
hub mcp [-h] [--hub HUB] [--token TOKEN] [--token-file TOKEN_FILE] [--path PATH]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--hub` |  | `http://localhost:8080` |
| `--token` | AVOID: use HUB_TOKEN or --token-file |  |
| `--token-file` |  |  |
| `--path` |  | `/mcp` |
