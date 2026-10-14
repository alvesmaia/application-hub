# App API and MCP

Every app exposes a JSON API at `/<id>/api/v1/*` and the `openapi.json` document.

- Each route has an `operation_id`: it becomes the app's MCP tool name in the hub MCP.
- Reads are `GET`; every mutation (`POST`/`PUT`/`PATCH`/`DELETE`) requires approval when called through MCP.
- Errors in RFC 9457 (`hub_sdk.api.problem`); without identity, `401`.
- `hub check` regenerates `openapi.json`; the package is refused without it.

## Use the hub MCP in an agent

```bash
hub mcp --hub @HUB@ --token-file ~/.hub-token
```

`hub mcp` is an MCP server over stdio that forwards to `@HUB@/mcp` with your token. Clients with OAuth 2.1 can
use `@HUB@/mcp` directly.

Details: Contract §23 and §24 ([Contract](/developers/contrato)).
