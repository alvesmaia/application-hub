# Deployment — Python hub (platform-py): new server

Short checklist. From `platform-py/`, Docker Compose >= 2.24.

**Shortcut:** `bash scripts/install.sh` in a terminal asks for each option (admin, port, bind, public URL, project,
network range, sample apps), shows a summary and asks for confirmation. With options it skips the questions but
still shows what it will do and asks `Continuar? (s/N)` (default no); `--yes` counts as that consent (required
without a terminal): `bash scripts/install.sh --yes --admin-email voce@empresa.com --public-url https://hub.empresa.com` does the
steps below (new secrets, images built from source, master key, first administrator, start-up). Then follow from the
item "Tunnel/TLS proxy" onwards. Updates: [`ATUALIZACAO.md`](ATUALIZACAO.md). Behind a corporate proxy, with an
inspecting CA, internal mirrors or without a systemd user session: [`REDE-CORPORATIVA.md`](REDE-CORPORATIVA.md)
(start with `install.sh --doctor`).

**Stack options** (in `.prod.env`; defaults = original installation): `HUB_PROJECT` (project prefix, containers,
volumes, networks and images; `app-hub`), `HUB_PORT` (8090), `HUB_BIND` (127.0.0.1), `HUB_SUBNET` (10.231 → networks
`10.231.<n>.0/24` for the apps), debug ports `HUB_APPDB_PORT`/`HUB_APP_PORT_<APP>`, `HUB_MINIO_IMAGE`/`HUB_MC_IMAGE`.
**Sample apps** (ativos, fornecedores, servicos, coletor) only start with `COMPOSE_PROFILES=demo-apps`; without them use
`HUB_APPS_JSON=./apps.empty.json` (empty catalog). The apps' database (`app-hub-app-db`) always starts.

**MinIO/mc:** there is no usable public image; `infra/minio/Dockerfile` builds from the official source code (pinned tags;
AGPL-3.0, a separate program used only over the network — see `NOTICE`). `install.sh` generates `<project>-mc` and `<project>-minio`;
manually: `docker build --target mc -t app-hub-mc:0.10.0 infra/minio` before `up --build` (the hub copies the `mc`).

**Ready-made images (GHCR):** the workflow `.github/workflows/images.yml` is **manual** (Actions › images › Run workflow,
input `version`) and publishes `ghcr.io/<owner>/app-hub-{web,minio,mc,ativos,fornecedores,servicos,coletor}:<version>`
(+ `latest`). To use: `HUB_VERSION=<version> docker compose --env-file .prod.env -f docker-compose.yml
-f docker-compose.apps.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml pull` and then `… up -d`
(`HUB_REGISTRY` changes the registry). GHCR packages start private: make them public in the package settings.

- [ ] `cp prod.env.example .prod.env && chmod 600 .prod.env`; fill in **all** required variables with new values per server (`openssl rand -hex 24`): `HUB_PUBLIC_URL` (https), `APP_HUB_DB_PASSWORD`,
      `APP_HUB_APP_DB_PASSWORD`, `APP_HUB_OBJSTORE_PASSWORD`, `HUB_INTERNAL_TOKEN_<APP>` (4). Optional: CIDRs
      (`HUB_ADMIN_ALLOWED_CIDRS`, `HUB_TRUSTED_PROXIES`, `HUB_BROKER_APP_CIDRS`), `HUB_USER_SIG_KEY`, telemetry,
      `HUB_SOURCE_URL` (GitHub link in the header; default the project repo, empty = hidden, only `https://`).
      **Do not** set `HUB_DEMO_USERS`/`HUB_DEMO_PASSWORD`.
- [ ] Start: `docker compose --env-file .prod.env -f docker-compose.yml -f docker-compose.apps.yml -f docker-compose.prod.yml up -d --build`
      (refuses to start if a variable is missing; Secure cookies; app test routes disabled). Sample apps: `COMPOSE_PROFILES=demo-apps` in `.prod.env`.
- [ ] Vault master key: created at `/data/master.key` on the first boot — copy it off the server (without it the encrypted secrets cannot be opened).
- [ ] First administrator (nothing secret in the log): `docker compose ... run --rm --no-deps app-hub-web python -m hub.admin create-admin --email voce@empresa.com --out /data/primeiro-acesso.txt`;
      read the temporary password from the file (`docker cp`), delete it from the volume; changing the password is mandatory on the first login.
- [ ] Tunnel/TLS proxy to `127.0.0.1:8090`; check `/healthz`, login, `/apps/<id>` and `/.well-known/oauth-authorization-server`.
- [ ] Backup before any change: volume `app-hub-data` + `pg_dumpall` of `app-hub-db` and `app-hub-app-db`.
- [ ] E2E/ui-sweep only on a temporary instance (`scripts/tmp-instance.sh`), never with demo accounts in production.

## Sizing (simultaneous users)

Measured with `platform/loadtest/py-load.mjs` on an isolated stack (`install.sh --project app-hub-load`), on 13/10/2026,
version 0.2.0. Script of a real person (Home, catalog, opening the ativos app, browsing, searching, Inbox) with ~2 s
pause between clicks; criteria p95 < 300 ms and errors < 0.1%.

| Simultaneous users | req/s | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|
| 25 | 11.8 | 12 ms | 87 ms | 112 ms | 0% |
| 50 | 23.4 | 13 ms | 83 ms | 112 ms | 0% |
| 100 | 46.9 | 18 ms | 120 ms | 218 ms | 0% |
| 200 | 84.4 | 129 ms | 1020 ms | 1361 ms | 0% (slow) |

**One instance serves ~100 to 200 simultaneous users** (on the order of 1,000 registered users with normal use
throughout the day). The limit is the hub running in a single process (one CPU core); memory stays at ~110 to 140 MiB.

Re-measured on 13/10/2026 (same machine, script and criteria; version with the replica fixes):

| Hub | Supports | Next step |
|---|---|---|
| 1 container | 200 users (p95 185 ms) | 300: p95 885 ms |
| 2 containers behind the edge (`round_robin`) | 300 users (p95 212 ms, 139 req/s) | 400: p95 671 ms |

More processes in the same container (`uvicorn --workers`) do **not** work: each process tries to open the internal broker
port (8081) and fails. To scale = more hub containers behind the edge, splitting the state database
(`HUB_STATE_DB_URL`) and the data volume. The compose file does not yet ship this mode ready (see `docs/PENDENCIAS.md`).
Repeat the measurement after changes to the gateway, the session or the database:

```bash
HUB=http://127.0.0.1:<port> HUB_DEMO_PASSWORD=… node platform/loadtest/py-load.mjs --steps 25,50,100,200
```

## Podman rootless (RHEL, Rocky, Fedora)

Tested on Rocky 9 with Podman 5.8, user without root/sudo (`docs/TESTE-IMPLANTACAO-RHEL-ROOTLESS.md`).

Ask IT (root, once):
- `dnf install podman podman-docker git` and `loginctl enable-linger <user>` (without linger the hub goes down on logout);
- delegate CPU to the user — RHEL 9 delegates only `memory pids`; without this the apps' CPU limits stay off:
  `/etc/systemd/system/user@.service.d/delegate.conf` with `[Service]` and `Delegate=cpu cpuset io memory pids`,
  then `systemctl daemon-reload` and a new login of the user.

As the user:
- `systemctl --user enable --now podman.socket`;
- compose without root: `install.sh` offers to download it (or run with `--get-compose`) — official binary at a fixed version,
  sha256 checked, in `~/.local/bin/docker-compose`; the `docker compose` from `podman-docker` uses it;
- `bash platform-py/scripts/install.sh …` detects rootless Podman and writes to `.prod.env`
  `HUB_CONTAINER_SOCKET_HOST=$XDG_RUNTIME_DIR/podman/podman.sock` and `DOCKER_GID=0`. With `--bind` outside loopback
  it also writes `HUB_BIND6` (Podman already occupies IPv6).

The hub detects Podman through the API: the apps' tmpfs with `U` (Podman does not accept `uid=`) and, without the `cpu`
controller delegated, builds and creates the containers without a CPU limit (memory and pids stay limited).

## Register the app in Entra ID

Sign-in with Microsoft (OIDC authorization code + PKCE, v2 endpoints). Optional: without configuration the hub shows only local login.

1. Azure portal › **Microsoft Entra ID › App registrations › New registration**. Name: the hub's name. Account types: "this directory only" (one tenant) or "any organizational directory" (multi-tenant).
2. **Redirect URI**, platform **Web**: `<HUB_PUBLIC_URL>/auth/entra/callback` (e.g. `https://hub.empresa.com.br/auth/entra/callback`). Also add `<HUB_PUBLIC_URL>/login` (return from single logout). They must match `HUB_PUBLIC_URL` exactly.
3. Copy **Application (client) ID** → `HUB_ENTRA_CLIENT_ID` and **Directory (tenant) ID** → `HUB_ENTRA_TENANT_ID` (multi-tenant: `organizations` + `HUB_ENTRA_ALLOWED_TENANTS`).
4. **Certificates & secrets › New client secret**: store the value in the hub's vault (recommended; with `app-hub-web` stopped:
   `docker compose ... run --rm --no-deps -T app-hub-web python -m hub.admin set-entra-secret < arquivo`, then delete the
   file) or in a file outside git (`chmod 600`) pointed to by `HUB_ENTRA_CLIENT_SECRET_FILE`. Note the expiry date and renew before it expires.
5. **API permissions**: `openid`, `profile`, `email` (Microsoft Graph, delegated) — already included by default.
6. Optional, roles by group: **Token configuration › Add groups claim** (security group IDs) or create **App roles** and assign them in *Enterprise applications*. Map them in `HUB_ENTRA_ROLE_MAP` (`{"<group objectId or app role value>":"Administrador"}`). With a mapping, the user's roles are synced on every login; with no mapped group, whoever signs in for the first time gets `HUB_ENTRA_DEFAULT_ROLE` (default `Colaborador`).
7. Set `HUB_SSO_PROVIDER=entra` and restart `app-hub-web`. Check it in **Administração › Configurações › Login único (SSO)** (Administration › Settings › Single sign-on (SSO)).

Rules: the id_token is validated (signature by the cached JWKS, issuer, audience, nonce, validity, tenant); `HUB_ENTRA_ALLOWED_DOMAINS` restricts e-mail domains; a user deactivated in the hub cannot sign in; the first sign-in creates the user (origin "Entra ID" in Usuários — Users) or links to a local user with the same e-mail. SSO sign-ins and refusals go to the audit trail. Local login stays on (`HUB_LOCAL_LOGIN=1`) so the administrator can sign in even if Entra fails; `HUB_LOCAL_LOGIN=admins` leaves e-mail/password only for administrators.

## Connect AI clients (MCP) with single sign-on

The hub is the OAuth 2.1 server for `/mcp` itself: the client registers on its own, opens the browser, the user signs in
(e-mail/password or "Sign in with Microsoft", if Entra is configured), approves the consent and the client receives
a **hub** token, valid only at `/mcp`. No extra scope in Entra: only `openid`, `profile`, `email`. The Entra
token stays in the hub; the client never calls Graph. Do not register the clients' `localhost` URIs in Entra.

Requirements: `HUB_PUBLIC_URL` on `https://` (it is the token issuer) and `[mcp.oauth] enabled = true` in the hub
configuration (default). In all clients the URL is `<HUB_PUBLIC_URL>/mcp`, **without** an `Authorization` header: without a token,
the client receives the 401 with the discovery and starts the sign-in in the browser.

- **Claude Code:** `claude mcp add --transport http application-hub https://hub.empresa.com.br/mcp`; then, in the session, `/mcp` › `application-hub` › *Authenticate*.
- **GitHub Copilot CLI:** in the session, `/mcp add` › type *HTTP* › URL `https://hub.empresa.com.br/mcp` (no headers) › save (Ctrl+S); on the first call the browser opens for sign-in. Via file
  (`~/.copilot/mcp-config.json`): `{"mcpServers": {"application-hub": {"type": "http", "url": "https://hub.empresa.com.br/mcp", "tools": ["*"]}}}`.
  In GitHub organizations, MCP use may need to be enabled by the organization's administrator.
- **Copilot in VS Code / desktop app:** *MCP: Add Server* › *HTTP* › the same URL; via file, `.vscode/mcp.json`
  with `{"servers": {"application-hub": {"type": "http", "url": "https://hub.empresa.com.br/mcp"}}}`. The client
  asks to authenticate and opens the browser.

The user sees and revokes the connections in **Conta › Acessos › Apps autorizados** (Account › Access › Authorized apps); the administrator sees the registered clients and the authorizations in **Credenciais › Clientes de IA** (Credentials › AI clients). Client without OAuth support: personal token (`hubt_…`) in the
`Authorization: Bearer` header. Scenario covered by `platform-py/tests/test_oauth_entra.py`.
