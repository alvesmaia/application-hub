# F4b - correcoes pos-sweep e /admin/tokens

1. **`/admin` 404**: faltava a pagina Configuracoes (Rust `admin_pages.rs`). Portada: `/admin` (preferencias, status publico, cofre) + `GET/PUT /api/admin/settings` + `POST /api/admin/vault/rotate-data-key` (`hub/admin/tokens.py`, `admin_settings.html`). Admin e operador 200, leitor 403.
2. **`#hub-admin-menu` para leitor**: menu filtrado por permissao (`visible_nav` em `hub/ux/shell.py`, usa `admin_required`); sem itens, o bloco (divisor + menu) nem e emitido (`base.html`). Leitor: 403 em `/admin*` e `/api/admin/*` (testado no pytest e ao vivo no :8090).
3. **Token interno 401 nos 5 apps**: causa = `docker-compose.apps.yml` fixava `HUB_INTERNAL_TOKEN=hubpy-local-<app>` enquanto o cofre do `hubpy-web` semeia de `HUB_INTERNAL_TOKEN_<APP>` do `.dev.env` (valores diferentes). Correcao: o compose dos apps usa `${HUB_INTERNAL_TOKEN_<APP>:-hubpy-local-<app>}` e `scripts/dev.sh` passa `--env-file .dev.env`. Apps recriados; ao vivo: busca federada devolve grupos dos apps e `tools/list` do `/mcp` traz `ativos.*`, `coletor.*`, `contratos.*`...
4. **`/admin/tokens`** (Acesso > Tokens de API + Clientes de IA/MCP + Autorizacoes ativas): `GET/POST /api/admin/tokens`, `POST .../{id}/revoke`, `DELETE .../{id}` (so revogado); `create_token` aceita `envs`. pt-BR no template, en/es pelo `page_response` (catalogo). Paridade (Rust :8080 x Py local :8095 semeado com os mesmos tokens/clientes): `/admin` e `/admin/tokens` PASS, DOM 0/0, pixels 0.00/0.00 (1440/390), tambem com o modal "Novo token" aberto. Ignores novos em `parity/ignore.json` (com `_why`): prefixo/id sorteados, "Ultimo uso", pilula de uso do cliente.

Testes: `tests/test_admin_f4b.py` (4). `ruff format` de `tests/test_graphsim.py` corrigido.

## Lacunas
- `ty` aponta 5 `unsound-return-statement` (`page_response`/`tr_html`) em arquivos da traducao i18n em andamento por outro agente (`hub/ux/texts.py` e rotas admin/ops/insight/connections); nao tocados aqui.
- Tokens: sem edicao; clientes OAuth "Em uso" dependem de uso real (ignorado na paridade).
- Rotacao da chave de dados regrava so o `admin.json` (como o Rust); conexoes recifram na proxima escrita.
