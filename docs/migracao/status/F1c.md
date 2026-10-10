# F1c — Auth Python

Portados em `platform-py/hub/auth/` e ligados ao app FastAPI:

- Leitura/escrita compatível de `auth.json`; Argon2id PHC (19 MiB, 2 iterações, 1 via); IDs e hashes de sessão/token; escrita atômica, `0600` e `file_gen`/lock de `hub-state`.
- Sessões em arquivo ou na tabela compartilhada `sessions`; migração de sessões antigas; rotação no login, limites de inatividade/duração, logout, invalidação em troca de senha; falha do DB retorna 503 ao validar sessão.
- Login por e-mail ou local-part único, política de senha, limitação por IP e IP/conta com janelas compartilhadas pelo state DB; IP e CIDR usam `HUB_TRUSTED_PROXIES` e `HUB_ADMIN_ALLOWED_CIDRS`.
- Papéis/permissões carregados do `admin.json` Rust, guard de rotas, identidade real para shell/gateway/WebSocket, tokens `hubt_` pessoais com ações, apps, validade, hash e limite por dono.
- Usuários demo e administrador bootstrap; páginas de login e troca de senha com CSP global e `frame-ancestors 'none'`.
- Vetores golden gerados pelo crate Rust para hash Argon2id, hash de sessão e hash de token.

Verificação: `bash platform-py/scripts/check.sh` com PostgreSQL descartável (`HUB_DB_TEST_URL`): Ruff, formato, mypy strict, **47 testes passaram**. `hubpy-testdb` parado após a execução. Sem DB, 4 testes DB são ignorados.

Limites: catálogo de papéis permanece no `admin.json` (como no Rust); este recorte não porta OIDC/SSO nem as rotas administrativas completas de gestão de usuários/funções. Não alterado código Rust persistente nem containers `hub-*`/`apphub-devtunnel`.
