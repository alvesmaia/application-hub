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

Novo app (interativo no terminal; não interativo com --yes ou sem TTY).

```
hub project create [-h] [--name NAME] [--stack STACK] [--db DB] [--requires REQUIRES] [--dir DIR] [--template {basic,dashboard}] [--with WITH_] [--yes] [--json] [id]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `id` | id do app (2–32: a-z, 0-9, '-') |  |
| `--name` | nome exibido (padrão: id capitalizado) |  |
| `--stack` | stack (veja `hub project stacks`; padrão python-fastapi) |  |
| `--db` | sqlite (padrão) ou URL do banco (vai para .env DATABASE_URL) |  |
| `--requires` | o que o hub provisiona: database,storage (padrão: database) |  |
| `--dir` | pasta destino (padrão: ./<id>) |  |
| `--template` (basic \| dashboard) | basic (padrão) ou dashboard (KPIs, gráficos, tabela, filtro e exportação) |  |
| `--with` | extras: dashboards (= --template dashboard) |  |
| `--yes`, `-y` | sem perguntas: usa os padrões |  |
| `--json` | saída em JSON |  |

Exemplos:

```
  hub project create estoque --stack python-fastapi --yes --json
  hub project create vendas --stack python-fastapi --template dashboard --yes
  hub project create estoque --name "Estoque" --requires database,storage --dir ./estoque --yes
  hub project create            (pergunta o que faltar, se houver terminal)
```

## `hub project stacks`

Lista as stacks disponíveis.

```
hub project stacks [-h] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--json` |  |  |

## `hub check`

ruff, ty, pytest+cobertura, manifesto, openapi e conformidade local.

```
hub check [-h] [--dir DIR] [--only ONLY] [--full] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--only` | etapas separadas por vírgula: manifesto,ruff,formato,ty,openapi,pytest,conformidade |  |
| `--full` | também a suíte completa do contrato (conformance.json) |  |
| `--json` |  |  |

Exemplos:

```
  hub check --only ruff,pytest --json
```

## `hub pack`

Gera o pacote .hubapp a partir da pasta do app.

```
hub pack [-h] [--dir DIR] [--image IMAGE] [--build-dir BUILD_DIR] [--dockerfile DOCKERFILE] [--out OUT]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--image` | imagem por digest (nome@sha256:…); alternativa: --build-dir |  |
| `--build-dir` | contexto de build: o hub constrói a imagem do pacote |  |
| `--dockerfile` | Dockerfile dentro de --build-dir | `Dockerfile` |
| `--out` |  |  |

## `hub dev`

Hub local leve (sem Docker) com o app dentro do shell.

```
hub dev [-h] [--dir DIR] [--upstream UPSTREAM] [--port PORT] [--role ROLE] [--limits] [command ...]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--dir` |  | `.` |
| `--upstream` | app já rodando (senão a CLI sobe o app do projeto) |  |
| `--port` |  | `4173` |
| `--role` |  | `admin` |
| `--limits` |  |  |
| `command` |  |  |

Exemplos:

```
  hub dev                       (projeto do template: sobe o app com recarga)
  hub dev --upstream http://127.0.0.1:9000
```

## `hub login`

Salva o hub e o token (o token é pedido sem eco).

```
hub login [-h] --hub HUB [--token TOKEN] [--token-file TOKEN_FILE] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--hub` |  (obrigatório) |  |
| `--token` | EVITE: use HUB_TOKEN, --token-file ou a pergunta sem eco |  |
| `--token-file` | arquivo com o token (ex.: entregue pelo cofre do CI) |  |
| `--json` |  |  |

Exemplos:

```
  hub login --hub https://hub.exemplo.com
```

## `hub deploy`

Empacota e envia ao hub (produção fica pendente de aprovação).

```
hub deploy [-h] --env {staging,production} [--dir DIR] [--hub HUB] [--token TOKEN] [--env-file ENV_FILE] [--yes] [--json]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--env` (staging \| production) |  (obrigatório) |  |
| `--dir` |  | `.` |
| `--hub` | URL do hub (padrão: o do `hub login` ou HUB_URL) |  |
| `--token` | EVITE: use `hub login` ou HUB_TOKEN |  |
| `--env-file` | valores de requires (padrão: deploy.<env>.env) |  |
| `--yes`, `-y` | sem perguntas |  |
| `--json` |  |  |

Exemplos:

```
  hub deploy --env staging
  hub deploy --env production --hub https://hub.exemplo.com --json
```

## `hub mcp`

Servidor MCP por stdio que repassa ao MCP do hub com um token.

```
hub mcp [-h] [--hub HUB] [--token TOKEN] [--token-file TOKEN_FILE] [--path PATH]
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--hub` |  | `http://localhost:8080` |
| `--token` | EVITE: use HUB_TOKEN ou --token-file |  |
| `--token-file` |  |  |
| `--path` |  | `/mcp` |
