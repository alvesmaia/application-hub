"""The public API operations table: the `/api/v1` routes, the OpenAPI and the MCP tools are generated from it.

Each operation is a **facade** over an internal route (`/api/ops|admin|me`, the same handlers as the UI, called in
process with the same identity) or a small native read (`native`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hub.api import schemas as sc

MULTIPART = "multipart"
S_STR: dict[str, Any] = {"type": "string"}
S_ID: dict[str, Any] = {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,80}$"}
S_INT: dict[str, Any] = {"type": "integer", "minimum": 0}
S_ENV: dict[str, Any] = {"type": "string", "enum": ["producao", "staging"]}
HEALTH_VIEW = ("editar_configuracoes", "ver_auditoria")


@dataclass(frozen=True)
class Param:
    name: str
    loc: str  # "path" | "query"
    schema: dict[str, Any]
    desc: str
    required: bool = False


@dataclass(frozen=True)
class Op:
    id: str
    path: str  # relativo a /api/v1
    tag: str
    summary: str
    resp: str
    method: str = "GET"
    params: tuple[Param, ...] = ()
    body: str | None = None
    target: tuple[str, str] | None = None  # (internal method, internal path with {param})
    native: str | None = None
    list: str | None = None  # key of the array in the internal response ("" = root); None = not a list
    filters: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()  # query parameters passed through to the internal route
    destructive: bool = False
    mcp: bool = True
    need: tuple[str, ...] = ()  # native only: any one of these permissions (empty = authenticated)

    @property
    def write(self) -> bool:
        return self.method != "GET"

    @property
    def full_path(self) -> str:
        return "/api/v1" + self.path


P_APP = Param("app", "path", S_ID, "Id do app (ex.: ativos)", True)
P_ID = Param("id", "path", S_ID, "Identificador", True)
P_ENV = Param("env", "query", S_ENV, "Ambiente (padrão: producao)")


def _named(name: str, desc: str) -> Param:
    return Param(name, "path", S_ID, desc, True)


def _tgt(method: str, path: str) -> dict[str, Any]:
    return {"target": (method, path)}


OPS: tuple[Op, ...] = (
    Op("apps_list", "/apps", "Apps", "Lista os apps que você alcança (catálogo)", "App", native="apps",
       list="", filters=("stack", "status")),
    Op("apps_get", "/apps/{app}", "Apps", "Detalhes de um app (manifesto, API, MCP)", "App",
       params=(P_APP,), native="app"),
    Op("apps_status", "/apps/{app}/status", "Apps", "Situação de um app: estado, incidentes abertos e SLOs",
       "AppStatus", params=(P_APP,), native="app_status"),
    Op("status_get", "/status", "Apps", "Status page: situação atual de cada app visível e incidentes abertos",
       "StatusPage", native="status"),
    Op("observability_summary", "/observability/summary", "Observabilidade",
       "Resumo de tráfego, erros e latência (p95) por app e ambiente", "Observability",
       params=(Param("minutes", "query", {"type": "integer", "enum": [15, 30, 60]}, "Janela (padrão 15)"),
               Param("app", "query", S_ID, "Só este app")),
       native="observability", need=HEALTH_VIEW),
    Op("logs_tail", "/logs", "Observabilidade", "Últimas linhas de log (plataforma e apps)", "LogLine",
       params=(Param("app", "query", S_ID, "Filtra por app"), Param("minutes", "query", S_INT, "Janela em minutos")),
       **_tgt("GET", "/api/admin/logs"), list="", filters=("level",), passed=("app", "minutes")),
    Op("deployments_list", "/deployments", "Implantações", "Implantações (histórico e pendentes de aprovação)",
       "Deployment", **_tgt("GET", "/api/ops/deployments"), list="", filters=("app", "env", "status")),
    Op("deployments_import", "/deployments", "Implantações",
       "Importa um pacote .hubapp (multipart: package, env) — roda o pipeline de 7 passos", "Job", method="POST",
       params=(P_ENV, Param("commit", "query", S_STR, "SHA do commit (CI)")), body=MULTIPART,
       **_tgt("POST", "/api/ops/packages"), passed=("env", "commit"), mcp=False),
    Op("deployments_approve", "/deployments/{id}/approve", "Implantações",
       "Aprova uma implantação de produção (troca o tráfego)", "Job", method="POST", params=(P_ID,),
       **_tgt("POST", "/api/ops/deployments/{id}/approve"), destructive=True),
    Op("deployments_promote", "/deployments/{id}/promote", "Implantações",
       "Promove a versão de staging para produção", "Job", method="POST", params=(P_ID,),
       **_tgt("POST", "/api/ops/deployments/{id}/promote"), destructive=True),
    Op("deployments_rollback", "/deployments/{id}/rollback", "Implantações", "Volta para a versão anterior",
       "Job", method="POST", params=(P_ID,), **_tgt("POST", "/api/ops/deployments/{id}/rollback"),
       destructive=True),
    Op("jobs_get", "/jobs/{id}", "Implantações",
       "Situação de um job assíncrono (implantação, backup, restauração)", "Job", params=(P_ID,),
       **_tgt("GET", "/api/ops/jobs/{id}")),
    Op("containers_list", "/containers", "Containers", "Containers dos apps (estado, recursos, versão)",
       "Container", **_tgt("GET", "/api/ops/runtime"), list="apps", filters=("app", "status")),
    Op("containers_start", "/apps/{app}/containers/start", "Containers", "Inicia o container do app",
       "ContainerAction", method="POST", params=(P_APP, P_ENV), passed=("env",),
       **_tgt("POST", "/api/ops/runtime/{app}/start")),
    Op("containers_stop", "/apps/{app}/containers/stop", "Containers",
       "Para o container do app (o app fica fora do ar)", "ContainerAction", method="POST",
       params=(P_APP, P_ENV), passed=("env",), **_tgt("POST", "/api/ops/runtime/{app}/stop"), destructive=True),
    Op("containers_restart", "/apps/{app}/containers/restart", "Containers", "Reinicia o container do app",
       "ContainerAction", method="POST", params=(P_APP, P_ENV), passed=("env",),
       **_tgt("POST", "/api/ops/runtime/{app}/restart"), destructive=True),
    Op("backups_list", "/backups", "Backups", "Histórico de backups", "Backup", **_tgt("GET", "/api/ops/backups"),
       list="history", filters=("app", "env", "status")),
    Op("backups_run", "/apps/{app}/backups", "Backups",
       "Faz um backup agora (banco + bucket + arquivos); devolve o job", "Job", method="POST",
       params=(P_APP, P_ENV), passed=("env",), **_tgt("POST", "/api/ops/backups/{app}/run")),
    Op("backups_restore", "/backups/{id}/restore", "Backups",
       "Restaura um backup (substitui os dados atuais; snapshot de segurança antes)", "Job", method="POST",
       params=(P_ID,), **_tgt("POST", "/api/ops/backups/records/{id}/restore"), destructive=True),
    Op("backups_rotate_credentials", "/backups/credentials/rotate", "Backups",
       "Rotaciona as credenciais só leitura dos backups (papel por banco, chave por bucket); as antigas deixam de valer",
       "RotateResult", method="POST", **_tgt("POST", "/api/ops/backups/credentials/rotate"), destructive=True),
    Op("tasks_list", "/tasks", "Tarefas", "Tarefas duráveis dos apps", "Task", **_tgt("GET", "/api/ops/tasks"),
       list="tasks", filters=("app", "status", "kind")),
    Op("tasks_retry", "/tasks/{app}/{id}/retry", "Tarefas", "Reprocessa uma tarefa", "TaskActionResult",
       method="POST", params=(P_APP, P_ID), **_tgt("POST", "/api/ops/tasks/{app}/{id}/retry")),
    Op("tasks_cancel", "/tasks/{app}/{id}/cancel", "Tarefas", "Cancela uma tarefa", "TaskActionResult",
       method="POST", params=(P_APP, P_ID), **_tgt("POST", "/api/ops/tasks/{app}/{id}/cancel"),
       destructive=True),
    Op("variables_list", "/variables", "Variáveis", "Variáveis de ambiente e vínculos (segredos mascarados)",
       "Variable", **_tgt("GET", "/api/admin/env"), list="", filters=("name", "secret")),
    Op("connections_list", "/connections", "Conexões", "Conexões de armazenamento e vínculos (sem credenciais)",
       "Connection", **_tgt("GET", "/api/admin/connections"), list="connections"),
    Op("connections_test", "/connections/{name}/test", "Conexões", "Testa uma conexão", "ConnectionTest",
       method="POST", params=(_named("name", "Nome da conexão"),),
       **_tgt("POST", "/api/admin/connections/{name}/test")),
    Op("storage_list", "/storage", "Conexões",
       "Armazenamentos provisionados pelo hub (bucket, usuário restrito e conexão por app × ambiente)", "Storage",
       **_tgt("GET", "/api/admin/connections/storage"), list="storage", filters=("app", "env")),
    Op("storage_provision", "/apps/{app}/storage", "Conexões",
       "Provisiona o armazenamento do app: bucket + usuário do MinIO restrito ao bucket + conexão S3 do broker (idempotente; desfaz em falha parcial)",
       "StorageChange", method="POST", params=(P_APP, P_ENV), passed=("env",),
       **_tgt("POST", "/api/admin/connections/storage/{app}")),
    Op("storage_deprovision", "/apps/{app}/storage/deprovision", "Conexões",
       "Desprovisiona o armazenamento do app (exige confirm = nome do bucket; bucket com objetos exige force e a senha numa sessão do navegador)",
       "StorageChange", method="POST", params=(P_APP, P_ENV), passed=("env",), body="DeprovisionRequest",
       **_tgt("POST", "/api/admin/connections/storage/{app}/deprovision"), destructive=True),
    Op("users_list", "/users", "Usuários", "Usuários", "User", **_tgt("GET", "/api/admin/users"), list="",
       filters=("email", "active")),
    Op("users_create", "/users", "Usuários", "Cria um usuário (senha provisória devolvida uma vez)", "User",
       method="POST", body="UserCreate", **_tgt("POST", "/api/admin/users"), mcp=False),
    Op("users_update", "/users/{id}", "Usuários", "Altera nome, funções ou situação de um usuário", "User",
       method="PUT", params=(P_ID,), body="UserUpdate", **_tgt("PUT", "/api/admin/users/{id}"), mcp=False),
    Op("roles_list", "/roles", "Usuários", "Funções e permissões", "Role", **_tgt("GET", "/api/admin/roles"),
       list=""),
    Op("audit_query", "/audit", "Auditoria", "Consulta a trilha de auditoria (mais recentes primeiro)",
       "AuditEntry", params=(Param("scope", "query", S_STR, "platform ou id do app"),),
       **_tgt("GET", "/api/admin/audit"), list="", filters=("actor",), passed=("scope",)),
    Op("usage_query", "/usage", "Auditoria", "Uso por usuário e app (aberturas, navegações, ações, último acesso)",
       "UsageRow", params=(Param("days", "query", {"type": "integer", "minimum": 1, "maximum": 400},
                                 "Período em dias (padrão 30)"),
                           Param("user", "query", S_STR, "Id do usuário"), Param("app", "query", S_STR, "Id do app")),
       **_tgt("GET", "/api/admin/usage"), list="rows", passed=("days", "user", "app")),
    Op("audit_verify", "/audit/verify", "Auditoria", "Verifica a integridade da cadeia de auditoria",
       "AuditVerify", method="POST", **_tgt("POST", "/api/admin/audit/verify")),
    Op("inbox_list", "/inbox", "Inbox", "Sua Inbox (notificações e pedidos de aprovação)", "InboxItem",
       **_tgt("GET", "/api/me/inbox"), list="items", filters=("kind", "app", "status")),
    Op("inbox_decide", "/inbox/{id}/decide", "Inbox", "Decide um pedido de aprovação (approve/reject)",
       "DecideResult", method="POST", params=(P_ID,), body="DecideRequest",
       **_tgt("POST", "/api/me/inbox/{id}/decide"), destructive=True),
    Op("search", "/search", "Busca", "Busca federada (plataforma + apps que você alcança)", "SearchResult",
       params=(Param("q", "query", {"type": "string", "minLength": 1, "maxLength": 100}, "Termo", True),),
       **_tgt("GET", "/api/me/search"), passed=("q",)),
)  # fmt: skip

BY_ID = {o.id: o for o in OPS}

TAGS = {
    "Apps": "Catálogo de apps, situação e status page",
    "Observabilidade": "Tráfego, erros, latência e logs",
    "Implantações": "Pacotes .hubapp, aprovação, promoção, rollback e jobs",
    "Containers": "Ciclo de vida dos containers dos apps",
    "Backups": "Backups, restauração e credenciais de backup",
    "Tarefas": "Tarefas duráveis e sagas dos apps",
    "Variáveis": "Variáveis de ambiente (segredos mascarados)",
    "Conexões": "Conexões de armazenamento e provisionamento",
    "Usuários": "Usuários e funções",
    "Auditoria": "Trilha de auditoria em cadeia",
    "Inbox": "Notificações e pedidos de aprovação",
    "Busca": "Busca federada",
}


def route(method: str, rel: str) -> tuple[Op, dict[str, str]] | bool:
    """Matches `method path` (relative to /api/v1). `False` = nothing; `True` = exists with another method (405)."""
    segs = rel.strip("/").split("/")
    hit = False
    for op in OPS:
        t = op.path.strip("/").split("/")
        if len(t) != len(segs):
            continue
        params: dict[str, str] = {}
        ok = True
        for a, b in zip(t, segs, strict=True):
            if a.startswith("{"):
                params[a.strip("{}")] = b
                ok = ok and bool(b)
            elif a != b:
                ok = False
        if ok:
            if op.method == method:
                return op, params
            hit = True
    return hit


def openapi() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    problem = {"$ref": "#/components/responses/Problem"}
    for op in OPS:
        params: list[dict[str, Any]] = [
            {"name": p.name, "in": p.loc, "required": p.required or p.loc == "path", "description": p.desc,
             "schema": p.schema}
            for p in op.params
        ]  # fmt: skip
        if op.list is not None:
            params += [
                {"name": "limit", "in": "query", "required": False, "description": "Itens por página",
                 "schema": {"type": "integer", "minimum": 1}},
                {"name": "offset", "in": "query", "required": False, "description": "Deslocamento",
                 "schema": {"type": "integer", "minimum": 0}},
            ]  # fmt: skip
            if "q" not in op.passed:
                params.append({"name": "q", "in": "query", "required": False,
                               "description": "Texto em qualquer campo", "schema": {"type": "string"}})  # fmt: skip
            params += [
                {"name": f, "in": "query", "required": False, "description": f"Filtra por {f}",
                 "schema": {"type": "string"}}
                for f in op.filters
            ]  # fmt: skip
        rref = {"$ref": f"#/components/schemas/{op.resp}"}
        ex = sc.example(op.resp) or {}
        if op.list is not None:
            ok: dict[str, Any] = {
                "description": "Página da lista",
                "content": {"application/json": {
                    "schema": {"allOf": [{"$ref": "#/components/schemas/Page"},
                                         {"type": "object",
                                          "properties": {"items": {"type": "array", "items": rref}}}]},
                    "example": {"items": [ex], "total": 1, "limit": 50, "offset": 0, "next": None}}},
            }  # fmt: skip
        else:
            ok = {"description": "Sucesso", "content": {"application/json": {"schema": rref, "example": ex}}}
        responses: dict[str, Any] = {"2XX" if op.write else "200": ok}
        codes = ["400", "401", "403", "404", "429"]
        if op.write:
            codes += ["409", "503"]
        if op.body:
            codes += ["413", "415"]
        for c in codes:
            responses[c] = problem
        o: dict[str, Any] = {
            "operationId": op.id, "summary": op.summary, "tags": [op.tag], "parameters": params,
            "responses": responses, "x-hub-destructive": op.destructive, "x-hub-mcp": op.mcp,
        }  # fmt: skip
        if op.body == MULTIPART:
            o["requestBody"] = {"required": True, "content": {"multipart/form-data": {"schema": {
                "type": "object", "required": ["package"],
                "properties": {"package": {"type": "string", "contentEncoding": "binary"},
                               "env": {"type": "string", "enum": ["producao", "staging"]}}}}}}  # fmt: skip
        elif op.body:
            o["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{op.body}"}, "example": sc.example(op.body) or {}}}}  # fmt: skip
        paths.setdefault(op.full_path, {})[op.method.lower()] = o
    tags = list(dict.fromkeys(o.tag for o in OPS))
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Application Hub — API pública",
            "version": "1.0.0",
            "description": "API versionada do hub. Mesmo RBAC da interface; erros no formato RFC 9457 (application/problem+json). Listas paginadas com limit/offset.",
        },
        "servers": [{"url": "/"}],
        "security": [{"bearer": []}, {"session": []}],
        "tags": [{"name": t, "description": TAGS.get(t, "")} for t in tags],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Token de API (hubt_…) com escopos api:read/api:write",
                },
                "session": {"type": "apiKey", "in": "cookie", "name": "hub_session"},
            },
            "schemas": sc.schemas(),
            "responses": {
                "Problem": {
                    "description": "Erro (RFC 9457)",
                    "content": {
                        "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                    },
                }
            },
        },
    }
