# Cliente MCP independente (SDK oficial TypeScript) — 2026-10-08T22:16:00.911Z

- Hub: http://127.0.0.1:8093
- SDK: @modelcontextprotocol/sdk (protocolo 2025-11-25)

- ✅ 401 com WWW-Authenticate resource_metadata — Bearer realm="hub", resource_metadata="http://127.0.0.1:8093/.well-known/oauth-protected-resource/mcp", scope="mcp api:write"
- ✅ SDK pediu autorização com PKCE S256 e resource (RFC 8707) — http://127.0.0.1:8093/mcp
- ✅ tela de consentimento do hub aberta — cliente "Verificação MCP (SDK oficial)"
- ✅ consentimento aprovado; retorno com code, state e iss (RFC 9207) — http://127.0.0.1:53682/callback
- ✅ SDK descobriu o servidor de autorização, registrou o cliente (DCR) e obteve o código — hubc_83dfcfb69c0cc926fdafd17962acb0a1
- ✅ código trocado por token (PKCE) com refresh token — escopo "mcp api:write", expira em 3600s
- ✅ initialize (Streamable HTTP) — servidor application-hub 0.0.0 · sessão sim
- ✅ tools/list — 62 ferramentas (27 de apps)
- ✅ tools/call apps_list (leitura) — {
  "items": [
    {
      "id": "ativos",
      "name": "Ge
- ✅ resources/list
- ✅ ferramenta destrutiva cria pedido de aprovação na Inbox (não executa) — aprovação i:8
- ✅ token vencido/inválido → SDK renovou com refresh (rotação), sem nova autorização
- ✅ reuso do refresh antigo → concessão revogada — reuso 400 · token atual 401
- ✅ token pessoal no cabeçalho (sem api:write: só leitura) — 46 ferramentas

**TODAS AS VERIFICAÇÕES PASSARAM**
