# Primeiro app

## Criar o projeto

```bash
hub project create estoque --name "Estoque" --stack python-fastapi --requires database --yes --json
```

Sem `--yes` e com terminal, a CLI pergunta o que faltar. Parâmetro inválido devolve o código `2`.

## O que o template traz

| Caminho | Conteúdo |
|---|---|
| `manifest.json` | id, nome, versão, rotas, `requires` (o que o hub provisiona) |
| `src/<pacote>/app.py` | FastAPI + `HubSdk` (prontidão, identidade, base pública) |
| `src/<pacote>/templates/` | telas em Jinja2 que estendem o workspace do SDK |
| `src/<pacote>/migrations/` | migrações Alembic (SQLite no dev, Postgres no hub) |
| `openapi.json` | não vem no template: o `hub check` gera a partir da API e regenera a cada mudança |
| `tests/` | pytest com piso de cobertura de 80% |
| `AGENTS.md` | regras para agentes de código e pessoas |

## Ciclo de desenvolvimento

```bash
hub dev
hub check
hub check --only ruff,pytest --json
```

## Testar à mão (curl)

As telas e as ações passam pelo hub em `/_frag/<id>/…` (o navegador vê `/apps/<id>/…`). Com o `hub dev`
no ar, o usuário simulado já está logado:

```bash
curl http://localhost:4173/_frag/estoque/itens
curl -X POST http://localhost:4173/_frag/estoque/itens      -H "Origin: http://localhost:4173"      -H "Idempotency-Key: $(date +%s%N)"      --data-urlencode "name=Parafuso" --data "quantity=7"
```

- `Origin` igual ao endereço do hub é **obrigatório** em POST, PUT, PATCH e DELETE. Sem ele, o hub responde
  403 ("Requisição bloqueada"), a proteção contra envio de outro site.
- `Idempotency-Key` é opcional e precisa ter de 8 a 128 caracteres (fora disso: 400). O navegador manda uma
  chave nova a cada envio. Repetir a mesma chave devolve a resposta guardada **sem executar de novo**.
- POST em `/apps/<id>/…` (o endereço da página) não executa a ação (404/405). Ações vão sempre para `/_frag/<id>/…`.
- A API JSON (`/apps/<id>/api/v1/…`) pede token: `-H "Authorization: Bearer <token>"`.

## Regras que o hub confere

- Links e `action` pela base pública (`{{ base }}`), nunca `/<id>/…` cru.
- Sem JavaScript próprio: comportamentos por atributos `data-hub-*` ([Padrões de tela](/developers/guias/telas)).
- Cores só por tokens do hub (`var(--…)`).
- `GET /<id>/_hub/ready` responde (o `HubSdk` já instala). É uma rota interna, que só o hub chama: pelo
  navegador ou pelo `/_frag`, o gateway devolve 404 para `/_hub`.
