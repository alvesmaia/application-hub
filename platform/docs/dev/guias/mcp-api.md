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

## Publish and operate through MCP

For an AI agent connected to the hub MCP that has no CLI token. The agent acts as the signed-in user, with the same
permissions as in the interface: publishing needs a role with **Editar configurações** (for example Tech Lead or Administrador).

### 1. Connect

- MCP URL: `@HUB@/mcp`. Clients with OAuth 2.1 sign in through the hub (discovery, dynamic registration and PKCE);
  grant both scopes, `mcp` and `api:write` (actions that change something).
- Without OAuth: a personal token from My account with the scopes `mcp` and `api:write`, sent as
  `Authorization: Bearer hubt_…` (or through `hub mcp --hub @HUB@ --token-file ~/.hub-token`).
- A token limited to some apps only publishes those apps.

When the hub sits behind an access proxy (for example Cloudflare Access), `/mcp/*` is the only path reachable from
outside; that is why the upload link below lives under `/mcp`.

### 2. Build the package

```bash
hub pack --build-dir .
```

### 3. Ask for an upload link

Call `deployments_upload` with `env` = `staging` or `producao` and, optionally, `commit` (the SHA, 7 to 40 hex).
The answer:

```json
{
  "upload_url": "@HUB@/mcp/uploads/<ticket>",
  "expires_at": 1760000600000,
  "method": "POST",
  "field": "package",
  "max_mb": 20,
  "env": "staging",
  "commit": null,
  "curl": "curl -fsS -F package=@<arquivo>.hubapp '@HUB@/mcp/uploads/<ticket>'",
  "next": "..."
}
```

The link is valid for 10 minutes (`expires_at`, epoch ms), works once and is bound to the user who asked for it. It
needs no session or bearer: the link itself is the credential, so never paste it in logs or shared places.

### 4. Send the package

Run the `curl` from the answer with the `.hubapp` path:

```bash
curl -fsS -F package=@app.hubapp '@HUB@/mcp/uploads/<ticket>?wait=true'
```

- `?wait=true` waits for the pipeline: `201` (ok) or `422` (failed), with the job. Without it: `202` with the job,
  running in the background.
- Variables and secrets from `requires` go as a `config` text field with `{"variables": {}, "secrets": {}}`
  (empty value = keep the hub's value): `-F 'config=<values.json'` (`<` sends the file content as text).

### 5. Follow the job

The answer is the job (`id`, `kind`, `steps`, `done`, `ok`, `message`, `result`). Call `jobs_get` with the job
`id` until `done`, and `deployments_list` (filters `app`, `env`, `status`) to see the deployment.

### 6. Staging, then production

- Staging: once the deployment is `Implantado`, open `@HUB@/apps/<id>` (or check with `apps_status`).
- Production: `deployments_promote` with the staging deployment `id` creates a production deployment
  `Aguardando aprovação`; a package sent with `env` = `producao` also waits. `deployments_approve` with that
  deployment `id` switches the traffic.
- Rollback: `deployments_rollback` with the active production deployment `id` goes back to the previous version (or,
  with an older `Substituído` deployment, back to that version).

### Confirmation of destructive tools

`deployments_promote`, `deployments_approve`, `deployments_rollback` and the other destructive tools work in two
calls:

1. The first call does nothing and answers `confirmation_required: true`, a `confirm_token` and `expires_in_s`
   (300 s by default).
2. Ask the user. Only after a yes, call the same tool again with the **same arguments** plus `confirm_token`.

The token is single use and bound to the user, the tool and the arguments: changed arguments, another tool or an
expired token are refused (ask again). The hub can also turn these tools off (`HUB_MCP_DESTRUCTIVE_ACTIONS=deny`):
then they do not appear in `tools/list`.

### Common errors

| Situation | What happens | What to do |
|---|---|---|
| Expired, used or unknown link | `404` "Link de envio inválido, vencido ou já usado" | Call `deployments_upload` again |
| Package over 20 MB | `413` | Shrink the package |
| Body not `multipart/form-data` | `415` | Send with `-F package=@…` |
| Too many uploads from one address | `429` with `retry-after` | Wait the seconds shown |
| User lost the permission or was deactivated, app outside the token's apps, address outside the admin network | `403` | Check the role, the token or the network with the hub owner |
| `deployments_upload` missing from `tools/list` | Token without `api:write`, or role without the permission; calling it answers "ferramenta desconhecida ou fora do seu acesso" | New sign-in or token with `api:write`, or a role that can publish |
| Pipeline failed | `422` (with `?wait=true`) or job with `ok: false` | Read `message` and the job steps; fix and send again with a new link |

The upload errors come as `{"error": "..."}`; with `-f` curl hides that body, so use `--fail-with-body` to read it.

### Other actions

The full tool list comes from `tools/list` (it already respects your role and token). Destructive tools carry
"AÇÃO DESTRUTIVA" in the description and need the confirmation above.

Tools by area (`*` = destructive, needs `confirm_token`):

| Area | Tools |
|---|---|
| Apps | `apps_list`, `apps_get`, `apps_status`, `status_get`, `search` |
| Observability | `observability_summary`, `logs_tail`, `logs_stats` |
| Deployments | `deployments_list`, `deployments_upload`, `deployments_approve*`, `deployments_promote*`, `deployments_rollback*`, `jobs_get` |
| Containers | `containers_list`, `containers_start`, `containers_stop*`, `containers_restart*`, `containers_set_limits`, `containers_apply*`, `containers_logs`, `containers_inspect`, `database_provision`, `replicas_get` |
| Backups | `backups_list`, `backups_run`, `backups_restore*`, `backups_rotate_credentials*`, `backups_set_policy*`, `backups_clone*`, `backups_test`, `backups_aside_list`, `backups_aside_restore*`, `backups_aside_drop*`, `maintenance_clear*` |
| Tasks | `tasks_list`, `tasks_retry`, `tasks_cancel*` |
| Variables | `variables_list`, `variables_create`, `variables_update`, `variables_delete*`, `variables_rotate*` |
| Connections and storage | `connections_list`, `connections_test`, `connections_create`, `connections_update`, `connections_delete*`, `connections_set_bindings*`, `connections_rotate*`, `connections_rotate_storage_token*`, `storage_list`, `storage_provision`, `storage_deprovision*` |
| Users and roles | `users_list`, `users_create`, `users_update*`, `users_delete*`, `users_unlock`, `roles_list`, `roles_create`, `roles_update`, `roles_delete*`, `roles_set_permission*`, `roles_set_apps*`, `grants_revoke*` |
| Audit and usage | `audit_query`, `audit_verify`, `usage_query`, `analytics_get`, `analytics_set_retention*`, `analytics_set_terms`, `analytics_rotate_key*` |
| Inbox | `inbox_list`, `inbox_decide*`, `inbox_mark_read`, `inbox_archive`, `access_request` |
| Settings and tokens | `settings_get`, `settings_update`, `tokens_list`, `tokens_revoke*`, `tokens_delete*` |
| Health | `health_list`, `health_create`, `health_check`, `health_update`, `health_delete*`, `slos_list`, `slos_set`, `slos_delete*`, `incidents_list`, `incidents_create`, `incidents_update` |
| Flags | `flags_list`, `flags_create`, `flags_update`, `flags_delete*`, `flags_set_enabled` |

Secrets are write-only: tools accept them and never return them. On purpose, MCP does not create API tokens, set
passwords, rotate the vault key or approve an app's own JavaScript: those stay on the hub screens.
