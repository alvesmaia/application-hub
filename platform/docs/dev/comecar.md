# Começar

Crie um app da plataforma, rode no seu computador e publique neste hub. Pré-requisito: **uv** e Python 3.12 —
sem Docker, sem Postgres, sem acesso ao repositório.

> **Agentes de código:** leiam [`/dev/llms.txt`](@HUB@/dev/llms.txt). Ele lista o Markdown original de cada página
> (`/dev/docs/<página>.md`), com o código completo. Um resumo do HTML perde os exemplos.

## Instalar a CLI

```bash
uv tool install hub-cli --index @HUB@/dev/cli/simple/
hub --help
```

O índice `@HUB@/dev/cli/simple/` é público e só leitura (wheels da `hub-cli` e do `hub-sdk`). Já instalada?
Atualize com `uv tool install --reinstall hub-cli --index @HUB@/dev/cli/simple/`. Hoje há uma stack:
`python-fastapi` (`hub project stacks`).

## Primeiro app em 5 comandos

```bash
hub project create estoque --stack python-fastapi --yes
cd estoque && uv sync
hub check
hub dev
hub deploy --env staging --yes
```

1. `hub project create` gera o app a partir do template (manifesto, telas, API, testes, `AGENTS.md`).
2. `uv sync` instala as dependências (o SDK vem em `vendor/`).
3. `hub check` roda lint, tipos, testes com cobertura, manifesto, `openapi.json` e conformidade local.
4. `hub dev` sobe um hub local com o app dentro do shell em `http://localhost:4173/apps/estoque`.
5. `hub deploy` empacota e envia; produção (`--env production`) fica pendente de aprovação.

## Token (só para publicar)

`project create`, `check` e `dev` funcionam sem login. Para publicar, [crie um token](@HUB@/admin/tokens) com as ações **envio** e **status** em Credenciais e salve-o na CLI (o token é
pedido sem eco e fica fora do projeto):

```bash
hub login --hub @HUB@
```

Em CI, use `--token-file` ou a variável `HUB_TOKEN`; nunca escreva o token em arquivos do projeto.

## Regras que o `hub check` cobra

- **API obrigatória** (Contrato §23): mantenha `install_api` no `app.py` e `"api": {"enabled": true}` no manifesto,
  mesmo que o app só tenha telas. Mudou rota? O `hub check` regenera o `openapi.json`; só o `pytest` falha com ele velho.
- **Formatação**: `uv run ruff format . && uv run ruff check --fix .` corrige o que o `hub check` aponta.
- **Sem JavaScript próprio**: comportamento de tela vem dos atributos `data-hub-*`
  ([Padrões de tela](/developers/guias/telas)); tempo real em [Tempo real](/developers/guias/tempo-real).
- `hub dev` recarrega ao mudar `src/`; mudou `pyproject.toml` ou `manifest.json`? `uv sync` e suba o `hub dev` de novo.

## Próximos passos

- [Referência da CLI](/developers/cli) e [do SDK](/developers/sdk)
- [Guias](/developers/guias): primeiro app, publicação, banco, armazenamento, telas, API e MCP
- [Contrato do app](/developers/contrato): o que o hub exige de todo app
