"""Second half of the operations table: the Administration, health, flags, connections and operations writes (and
the reads that go with them), all facades over the internal routes the screens use. Destructive = deletes, stops or
recreates, revokes, rotates a credential or key, swaps traffic or data, shrinks retention or deactivates someone."""

from __future__ import annotations

from hub.api.ops_model import P_APP, P_ENV, P_ID, S_ENV, S_NAME, S_NUM, Op, Param, _named, _tgt

P_ROLE = Param("name", "path", S_NAME, "Nome da função (ex.: Tech Lead)", True)
P_SVC = Param("name", "path", S_NAME, "Nome do serviço monitorado", True)
P_VAR = _named("name", "Nome da variável (ex.: API_URL)")
P_CONN = _named("name", "Nome da conexão")
P_KEY = _named("key", "Chave da flag")
P_ENV_PATH = Param("env", "path", S_ENV, "Ambiente", True)
P_NUM = Param("id", "path", S_NUM, "Número do incidente", True)

_USERS: tuple[Op, ...] = (
    Op("users_delete", "/users/{id}", "Usuários", "Exclui um usuário (sessões e tokens dele deixam de valer)",
       "Done", method="DELETE", params=(P_ID,), **_tgt("DELETE", "/api/admin/users/{id}"), destructive=True),
    Op("users_unlock", "/users/{id}/unlock", "Usuários", "Desbloqueia um usuário (zera as tentativas de login)",
       "User", method="POST", params=(P_ID,), **_tgt("POST", "/api/admin/users/{id}/unlock")),
    Op("roles_create", "/roles", "Usuários", "Cria uma função (sem permissões; conceda depois)", "Role",
       method="POST", body="RoleCreate", **_tgt("POST", "/api/admin/roles")),
    Op("roles_update", "/roles/{name}", "Usuários", "Altera descrição e nível de uma função", "Role",
       method="PUT", params=(P_ROLE,), body="RoleUpdate", **_tgt("PUT", "/api/admin/roles/{name}")),
    Op("roles_delete", "/roles/{name}", "Usuários", "Exclui uma função", "Done", method="DELETE",
       params=(P_ROLE,), **_tgt("DELETE", "/api/admin/roles/{name}"), destructive=True),
    Op("roles_set_permission", "/roles/{name}/permissions/{index}", "Usuários",
       "Concede ou revoga uma permissão da função (índice 0 a 5, na ordem de roles_list)", "Role", method="PUT",
       params=(P_ROLE, Param("index", "path", {"type": "integer", "minimum": 0, "maximum": 5}, "Índice", True)),
       body="PermissionGrant", **_tgt("PUT", "/api/admin/roles/{name}/permissions/{index}"), destructive=True),
    Op("roles_set_apps", "/roles/{name}/apps", "Usuários",
       "Define os apps que a função alcança (null = todos)", "Role", method="PUT", params=(P_ROLE,),
       body="RoleApps", **_tgt("PUT", "/api/admin/roles/{name}/apps"), destructive=True),
    Op("grants_revoke", "/grants/revoke", "Usuários", "Revoga um acesso concedido por pedido aprovado na Inbox",
       "DecideResult", method="POST", body="GrantRevoke", **_tgt("POST", "/api/admin/grants/revoke"),
       destructive=True),
)  # fmt: skip

_VARIABLES: tuple[Op, ...] = (
    Op("variables_create", "/variables", "Variáveis", "Cria uma variável de ambiente (segredo: o valor não volta)",
       "Variable", method="POST", body="VariableCreate", **_tgt("POST", "/api/admin/env")),
    Op("variables_update", "/variables/{name}", "Variáveis",
       "Altera uma variável (value vazio mantém o valor; os vínculos enviados substituem os atuais)", "Variable",
       method="PUT", params=(P_VAR,), body="VariableUpdate", **_tgt("PUT", "/api/admin/env/{name}")),
    Op("variables_delete", "/variables/{name}", "Variáveis", "Exclui uma variável de ambiente", "Done",
       method="DELETE", params=(P_VAR,), **_tgt("DELETE", "/api/admin/env/{name}"), destructive=True),
    Op("variables_rotate", "/apps/{app}/rotate/{what}", "Variáveis",
       "Rotaciona o token interno ou a senha do banco do app (o valor novo não volta; recrie o container)",
       "SecretRotated", method="POST",
       params=(P_APP, Param("what", "path", {"type": "string", "enum": ["token", "database"]}, "O quê", True),
               P_ENV),
       passed=("env",), **_tgt("POST", "/api/ops/rotate/{app}/{what}"), destructive=True),
)  # fmt: skip

_SETTINGS: tuple[Op, ...] = (
    Op("settings_get", "/settings", "Configurações", "Configurações da plataforma (nome, marca, status público)",
       "Settings", **_tgt("GET", "/api/admin/settings")),
    Op("settings_update", "/settings", "Configurações", "Altera as configurações da plataforma", "Settings",
       method="PUT", body="SettingsUpdate", **_tgt("PUT", "/api/admin/settings")),
    Op("tokens_list", "/tokens", "Configurações", "Tokens de API e de CI (só metadados; o segredo nunca volta)",
       "ApiToken", **_tgt("GET", "/api/admin/tokens"), list="", filters=("active", "owner")),
    Op("tokens_revoke", "/tokens/{id}/revoke", "Configurações", "Revoga um token de API", "ApiToken",
       method="POST", params=(P_ID,), **_tgt("POST", "/api/admin/tokens/{id}/revoke"), destructive=True),
    Op("tokens_delete", "/tokens/{id}", "Configurações", "Exclui um token de API (revoga se ainda ativo)", "Done",
       method="DELETE", params=(P_ID,), **_tgt("DELETE", "/api/admin/tokens/{id}"), destructive=True),
)  # fmt: skip

_HEALTH: tuple[Op, ...] = (
    Op("health_list", "/health", "Saúde", "Serviços monitorados e a última verificação", "HealthService",
       **_tgt("GET", "/api/admin/health"), list=""),
    Op("health_create", "/health", "Saúde", "Adiciona um serviço ao monitoramento", "HealthService",
       method="POST", body="HealthServiceCreate", **_tgt("POST", "/api/admin/health")),
    Op("health_check", "/health/check", "Saúde", "Verifica agora a disponibilidade dos apps", "HealthCheck",
       method="POST", **_tgt("POST", "/api/admin/health/check")),
    Op("health_update", "/health/{name}", "Saúde", "Altera tipo, situação ou app de um serviço monitorado",
       "HealthService", method="PUT", params=(P_SVC,), body="HealthServiceUpdate",
       **_tgt("PUT", "/api/admin/health/{name}")),
    Op("health_delete", "/health/{name}", "Saúde", "Remove um serviço do monitoramento", "Done",
       method="DELETE", params=(P_SVC,), **_tgt("DELETE", "/api/admin/health/{name}"), destructive=True),
    Op("slos_list", "/slos", "Saúde", "SLOs definidos (app × ambiente)", "Slo", **_tgt("GET", "/api/admin/slos"),
       list="", filters=("app", "env")),
    Op("slos_set", "/slos", "Saúde", "Define (ou substitui) o SLO de um app num ambiente", "Slo", method="PUT",
       body="SloSet", **_tgt("PUT", "/api/admin/slos")),
    Op("slos_delete", "/slos/{app}/{env}", "Saúde", "Remove o SLO de um app num ambiente", "Done",
       method="DELETE", params=(P_APP, P_ENV_PATH), **_tgt("DELETE", "/api/admin/slos/{app}/{env}"),
       destructive=True),
    Op("incidents_list", "/incidents", "Saúde", "Incidentes (abertos e resolvidos)", "Incident",
       **_tgt("GET", "/api/admin/incidents"), list="", filters=("app", "env", "severity")),
    Op("incidents_create", "/incidents", "Saúde", "Registra um incidente (aparece na status page)", "Incident",
       method="POST", body="IncidentCreate", **_tgt("POST", "/api/admin/incidents")),
    Op("incidents_update", "/incidents/{id}", "Saúde", "Acrescenta uma nota a um incidente ou o resolve",
       "Incident", method="POST", params=(P_NUM,), body="IncidentUpdate",
       **_tgt("POST", "/api/admin/incidents/{id}")),
)  # fmt: skip

_FLAGS: tuple[Op, ...] = (
    Op("flags_list", "/flags", "Flags", "Feature flags dos apps", "Flag", **_tgt("GET", "/api/admin/flags"),
       list="flags", filters=("app", "key")),
    Op("flags_create", "/flags", "Flags", "Cria uma feature flag", "Flag", method="POST", body="FlagCreate",
       **_tgt("POST", "/api/admin/flags")),
    Op("flags_update", "/flags/{app}/{key}", "Flags", "Substitui a definição de uma feature flag", "Flag",
       method="PUT", params=(P_APP, P_KEY), body="FlagUpdate", **_tgt("PUT", "/api/admin/flags/{app}/{key}")),
    Op("flags_delete", "/flags/{app}/{key}", "Flags", "Exclui uma feature flag", "Done", method="DELETE",
       params=(P_APP, P_KEY), **_tgt("DELETE", "/api/admin/flags/{app}/{key}"), destructive=True),
    Op("flags_set_enabled", "/flags/{app}/{key}/{env}/enabled", "Flags",
       "Liga ou desliga uma flag num ambiente (kill switch)", "Flag", method="POST",
       params=(P_APP, P_KEY, P_ENV_PATH), body="FlagEnabled",
       **_tgt("POST", "/api/admin/flags/{app}/{key}/{env}/enabled")),
)  # fmt: skip

_ANALYTICS: tuple[Op, ...] = (
    Op("analytics_get", "/analytics", "Auditoria", "Analytics de uso: resumo, retenção e coleta", "Analytics",
       **_tgt("GET", "/api/admin/analytics")),
    Op("analytics_set_retention", "/analytics/retention", "Auditoria",
       "Define a retenção do analytics em dias (7 a 400; encurtar apaga o que ficou mais velho)", "RetentionSet",
       method="PUT", body="AnalyticsRetention", **_tgt("PUT", "/api/admin/analytics/retention"), destructive=True),
    Op("analytics_set_terms", "/analytics/terms", "Auditoria",
       "Guarda (opt-in) ou não o texto das buscas sem resultado", "TermsSet", method="PUT",
       body="AnalyticsTerms", **_tgt("PUT", "/api/admin/analytics/terms")),
    Op("analytics_rotate_key", "/analytics/rotate-key", "Auditoria",
       "Troca a chave dos ids do analytics (ids novos não se ligam aos anteriores)", "Done", method="POST",
       **_tgt("POST", "/api/admin/analytics/rotate-key"), destructive=True),
    Op("logs_stats", "/logs/stats", "Observabilidade", "Quantos clientes acompanham os logs ao vivo", "LogStats",
       **_tgt("GET", "/api/admin/logs/stats")),
)  # fmt: skip

_CONNECTIONS: tuple[Op, ...] = (
    Op("connections_create", "/connections", "Conexões",
       "Cria uma conexão (SharePoint, S3 ou banco SQL); o segredo vai para o cofre e nunca volta", "Connection",
       method="POST", body="ConnectionCreate", **_tgt("POST", "/api/admin/connections")),
    Op("connections_update", "/connections/{name}", "Conexões",
       "Altera uma conexão: envie a configuração inteira, como na criação (sem secret mantém o atual)", "Connection", method="PUT", params=(P_CONN,),
       body="ConnectionUpdate", **_tgt("PUT", "/api/admin/connections/{name}")),
    Op("connections_delete", "/connections/{name}", "Conexões", "Exclui uma conexão", "Done", method="DELETE",
       params=(P_CONN,), **_tgt("DELETE", "/api/admin/connections/{name}"), destructive=True),
    Op("connections_set_bindings", "/connections/{name}/bindings", "Conexões",
       "Substitui os vínculos (app × ambiente) da conexão; quem sai perde o acesso", "Connection", method="PUT",
       params=(P_CONN,), body="BindingsSet", **_tgt("PUT", "/api/admin/connections/{name}/bindings"),
       destructive=True),
    Op("connections_rotate", "/connections/{name}/rotate", "Conexões",
       "Troca a credencial da conexão (segredo ou certificado)", "Connection", method="POST", params=(P_CONN,),
       body="ConnectionRotate", **_tgt("POST", "/api/admin/connections/{name}/rotate"), destructive=True),
    Op("connections_rotate_storage_token", "/connections/storage-tokens/{app}/{env}/rotate", "Conexões",
       "Rotaciona o token do broker de armazenamento do app (recrie o container)", "Done", method="POST",
       params=(P_APP, P_ENV_PATH), **_tgt("POST", "/api/admin/connections/tokens/{app}/{env}/rotate"),
       destructive=True),
)  # fmt: skip

_OPS: tuple[Op, ...] = (
    Op("containers_set_limits", "/apps/{app}/limits", "Containers",
       "Define CPU, memória e processos do app (aplicado aos containers em execução)", "LimitsResult",
       method="PUT", params=(P_APP,), body="ContainerLimits", **_tgt("PUT", "/api/ops/runtime/{app}/limits")),
    Op("containers_apply", "/apps/{app}/containers/apply", "Containers",
       "Recria o container com a mesma imagem e as variáveis atuais (blue/green)", "ContainerAction",
       method="POST", params=(P_APP, P_ENV), passed=("env",), **_tgt("POST", "/api/ops/runtime/{app}/apply"),
       destructive=True),
    Op("containers_logs", "/apps/{app}/containers/logs", "Containers", "Últimas linhas de log do container",
       "ContainerLogs", params=(P_APP, P_ENV, Param("tail", "query", {"type": "integer", "minimum": 1,
                                                                    "maximum": 2000}, "Linhas (padrão 200)")),
       passed=("env", "tail"), **_tgt("GET", "/api/ops/runtime/{app}/logs")),
    Op("containers_inspect", "/apps/{app}/containers/inspect", "Containers",
       "docker inspect do container (variáveis de ambiente mascaradas)", "ContainerInspect",
       params=(P_APP, P_ENV), passed=("env",), **_tgt("GET", "/api/ops/runtime/{app}/inspect")),
    Op("database_provision", "/apps/{app}/database", "Containers",
       "Provisiona (ou verifica) o banco do app e a variável de conexão (idempotente)", "DatabaseProvision",
       method="POST", params=(P_APP, P_ENV), passed=("env",), **_tgt("POST", "/api/ops/provision/{app}/database")),
    Op("replicas_get", "/replicas", "Containers", "Réplicas do hub vivas e quem lidera as tarefas", "Replicas",
       **_tgt("GET", "/api/ops/replicas")),
    Op("backups_set_policy", "/apps/{app}/backups/policy", "Backups",
       "Define a política de backup do app (agenda, retenção, volumes); reduzir keep apaga os mais antigos",
       "BackupPolicy", method="PUT", params=(P_APP,), body="BackupPolicyIn",
       **_tgt("PUT", "/api/ops/backups/policies/{app}"), destructive=True),
    Op("backups_clone", "/backups/{id}/clone", "Backups",
       "Clona um backup de produção para staging (substitui os dados de staging)", "Job", method="POST",
       params=(P_ID,), **_tgt("POST", "/api/ops/backups/records/{id}/clone"), destructive=True),
    Op("backups_test", "/backups/{id}/test", "Backups",
       "Testa a restauração de um backup num banco temporário (não toca nos dados)", "Job", method="POST",
       params=(P_ID,), **_tgt("POST", "/api/ops/backups/records/{id}/test")),
    Op("backups_aside_list", "/backups/aside", "Backups",
       "Bancos postos de lado por restaurações (para devolver ou apagar)", "BackupsAside",
       **_tgt("GET", "/api/ops/backups/aside")),
    Op("backups_aside_restore", "/backups/aside/{name}/rename-back", "Backups",
       "Devolve um banco posto de lado ao nome original (troca os dados atuais por ele)", "AsideResult",
       method="POST", params=(_named("name", "Nome do banco de lado"),),
       **_tgt("POST", "/api/ops/backups/aside/{name}/rename-back"), destructive=True),
    Op("backups_aside_drop", "/backups/aside/{name}/drop", "Backups", "Apaga um banco posto de lado",
       "AsideResult", method="POST", params=(_named("name", "Nome do banco de lado"),),
       **_tgt("POST", "/api/ops/backups/aside/{name}/drop"), destructive=True),
    Op("maintenance_clear", "/apps/{app}/maintenance/clear", "Backups",
       "Libera o app da janela de manutenção (o tráfego volta)", "Done", method="POST", params=(P_APP, P_ENV),
       passed=("env",), **_tgt("POST", "/api/ops/maintenance/{app}/clear"), destructive=True),
)  # fmt: skip

_ME: tuple[Op, ...] = (
    Op("inbox_mark_read", "/inbox/read", "Inbox", "Marca itens da sua Inbox como lidos (sem ids: todos)",
       "InboxChange", method="POST", body="InboxIds", **_tgt("POST", "/api/me/inbox/read")),
    Op("inbox_archive", "/inbox/archive", "Inbox", "Arquiva (ou desarquiva) itens da sua Inbox", "InboxChange",
       method="POST", body="InboxArchive", **_tgt("POST", "/api/me/inbox/archive")),
    Op("access_request", "/access-requests", "Inbox", "Pede acesso a um app (vai para a Inbox de quem aprova)",
       "AccessRequestResult", method="POST", body="AccessRequest", **_tgt("POST", "/api/me/access-requests")),
)  # fmt: skip

ADMIN_OPS: tuple[Op, ...] = (*_USERS, *_VARIABLES, *_SETTINGS, *_HEALTH, *_FLAGS, *_ANALYTICS, *_CONNECTIONS,
                             *_OPS, *_ME)  # fmt: skip

ADMIN_TAGS = {
    "Configurações": "Configurações da plataforma e tokens de API",
    "Saúde": "Serviços monitorados, SLOs e incidentes",
    "Flags": "Feature flags dos apps",
}
