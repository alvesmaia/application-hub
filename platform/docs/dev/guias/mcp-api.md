# API e MCP do app

Todo app expõe uma API JSON em `/<id>/api/v1/*` e o documento `openapi.json`.

- Cada rota tem `operation_id`: vira o nome da ferramenta MCP do app no MCP do hub.
- Leituras são `GET`; toda mutação (`POST`/`PUT`/`PATCH`/`DELETE`) exige aprovação quando chamada por MCP.
- Erros em RFC 9457 (`hub_sdk.api.problem`); sem identidade, `401`.
- `hub check` regenera o `openapi.json`; o pacote é recusado sem ele.

## Usar o MCP do hub num agente

```bash
hub mcp --hub @HUB@ --token-file ~/.hub-token
```

`hub mcp` é um servidor MCP por stdio que repassa ao `@HUB@/mcp` com o seu token. Clientes com OAuth 2.1 podem
usar `@HUB@/mcp` direto.

Detalhes: Contrato §23 e §24 ([Contrato](/developers/contrato)).
