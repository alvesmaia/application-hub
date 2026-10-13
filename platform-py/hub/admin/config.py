"""Variáveis de ambiente e funções da Administração (`hub-admin::store`): regras, mascaramento e operações."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hub.admin.store import AdminStore

PERMISSIONS = (
    "Acessar aplicações",
    "Gerenciar usuários",
    "Editar configurações da plataforma",
    "Ver auditoria e logs",
    "Gerenciar variáveis de ambiente",
    "Aprovar solicitações",
)
LEVELS = ("Total", "Alto", "Padrão")
ADMIN_ROLE = "Administrador"
APP_ENVS = (("staging", "Staging"), ("producao", "Produção"))
KINDS = ("publica", "secreta", "connection_string")
KIND_LABEL = {"publica": "Pública", "secreta": "Secreta", "connection_string": "Connection string"}
RESERVED_ENV = ("HUB_APP_ID", "PORT", "HUB_SCRATCH_DIR", "TMPDIR")
INTERNAL_TOKEN_ENV = "HUB_INTERNAL_TOKEN"  # noqa: S105
PLATFORM_ENV = (INTERNAL_TOKEN_ENV, "HUB_STORAGE_TOKEN", "HUB_STORAGE_URL", "HUB_CONNECTIONS")
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DEFAULT_ROLES = (
    (ADMIN_ROLE, "Acesso completo à plataforma e a todas as aplicações", 1, "Total", (1, 1, 1, 1, 1, 1)),
    ("Product Owner", "Gerencia produtos, projetos e times digitais", 4, "Alto", (1, 0, 0, 1, 0, 1)),
    (
        "Tech Lead",
        "Acesso técnico a ambientes, deployments e configurações",
        3,
        "Alto",
        (1, 0, 1, 1, 1, 0),
    ),
    (
        "Colaborador",
        "Acesso às aplicações do dia a dia, sem áreas administrativas",
        20,
        "Padrão",
        (1, 0, 0, 0, 0, 0),
    ),
)
# Variáveis iniciais do mockup (valem para os três apps originais, em produção salvo a última).
_DEFAULT_ENV = (
    ("API_BASE_URL", "https://api.platform.internal", "publica", "all", "producao"),
    ("AUTH_CLIENT_ID", "sk_live_7f3ac82e9b3f9a", "secreta", "all", "producao"),
    ("LOG_LEVEL", "info", "publica", "all", "producao"),
    ("FEATURE_FLAG_NEW_DASHBOARD", "true", "publica", ("fornecedores",), "producao"),
    (
        "NOTIFICATIONS_WEBHOOK_URL",
        "https://hooks.platform.internal/notify",
        "publica",
        ("servicos",),
        "producao",
    ),
    ("ASSET_SYNC_INTERVAL_MIN", "15", "publica", ("ativos",), "staging"),
)
_ORIGINAL_APPS = ("ativos", "fornecedores", "servicos")


# Senha de dev da pilha (compose) -> variável de ambiente com a senha rotacionada (`.prod.env`).
DEV_INFRA_PASSWORDS = {
    "APP_HUB_DB_PASSWORD": "hubpy-dev",
    "APP_HUB_APP_DB_PASSWORD": "hubpy-app-dev",
    "APP_HUB_OBJSTORE_PASSWORD": "hubpy-objstore-dev",
}


class AdminError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def valid_env_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name))


def is_platform_env(name: str) -> bool:
    return any(name == p or name.startswith(p + "_") for p in PLATFORM_ENV)


def env_suffix(app: str) -> str:
    return app.upper().replace("-", "_")


def env_label(env: str) -> str:
    return dict(APP_ENVS).get(env, "?")


def is_secret(var: dict[str, Any]) -> bool:
    return bool(var.get("kind", "publica") != "publica")


def injected_name(var: dict[str, Any]) -> str:
    return str(var.get("inject_as") or var["name"])


def display_value(var: dict[str, Any]) -> str:
    kind, value = var.get("kind", "publica"), str(var["value"])
    if kind == "publica":
        return str(value)
    if kind == "connection_string" or len(value) <= 8:
        return "••••••••"
    return "••••••••" + value[-4:]


def masked(var: dict[str, Any]) -> dict[str, Any]:
    """Cópia para listagens, na ordem de campos da API da versão anterior do hub; o valor de segredos nunca sai daqui."""
    out: dict[str, Any] = {
        "name": var["name"],
        "value": display_value(var),
        "kind": var.get("kind", "publica"),
        "bindings": var.get("bindings", []),
    }
    if var.get("inject_as"):
        out["inject_as"] = var["inject_as"]
    out["managed"] = bool(var.get("managed"))
    return out


def bound_to(var: dict[str, Any], app: str, env: str | None = None) -> bool:
    return any(b["app"] == app and (env is None or b["env"] == env) for b in var.get("bindings", []))


def resolve(vars_: list[dict[str, Any]], app: str, env: str) -> list[dict[str, Any]]:
    """Variáveis que entram no container de `app` em `env` (com valores: só para a injeção)."""
    out = [
        {"name": injected_name(v), "value": v["value"], "secret": is_secret(v), "source": v["name"]}
        for v in vars_
        if bound_to(v, app, env)
    ]
    return sorted(out, key=lambda r: r["name"])


def _resolved_map(vars_: list[dict[str, Any]], app: str) -> list[tuple[str, str, str]]:
    return [(e, r["name"], r["value"]) for e, _ in APP_ENVS for r in resolve(vars_, app, e)]


def _detail(var: dict[str, Any]) -> str:
    binds = (
        ", ".join(f"{b['app']} · {env_label(b['env'])}" for b in var.get("bindings", [])) or "sem vínculos"
    )
    return f"{var['name']} ({KIND_LABEL[var.get('kind', 'publica')]}) → {binds}"


def _scope(bindings: list[dict[str, str]]) -> str:
    apps = sorted({b["app"] for b in bindings})
    return apps[0] if len(apps) == 1 else "platform"


def _conflicts(vars_: list[dict[str, Any]], v: dict[str, Any]) -> None:
    for other in vars_:
        if other["name"] == v["name"] or injected_name(other) != injected_name(v):
            continue
        for b in v["bindings"]:
            if b in other.get("bindings", []):
                raise AdminError(
                    f"{other['name']} já entra como {injected_name(v)} em {b['app']} ({env_label(b['env'])})."
                )


def seed(data: dict[str, Any], apps: list[str], with_env: bool = True) -> bool:
    """Dados iniciais (variáveis e funções) quando o `admin.json` ainda não os tem; as variáveis só com cofre."""
    changed = False
    if with_env and not data.get("env") and not data.get("env_seeded"):
        for name, value, kind, scope, env in _DEFAULT_ENV:
            targets = [a for a in _ORIGINAL_APPS if a in apps] if scope == "all" else list(scope)
            bindings = sorted(({"app": a, "env": env} for a in targets), key=lambda b: (b["app"], b["env"]))
            data.setdefault("env", []).append(
                {"name": name, "value": value, "kind": kind, "bindings": bindings, "managed": False}
            )
        data["env_seeded"] = changed = True
    if not data.get("roles"):
        data["roles"] = [
            {"name": n, "description": d, "members": m, "level": lv, "permissions": [bool(x) for x in p]}
            for n, d, m, lv, p in _DEFAULT_ROLES
        ]
        changed = True
    data.setdefault("settings", {})
    data.setdefault("pending_restart", {})
    return changed


class ConfigOps:
    """Operações de variáveis e funções sobre o `admin.json` (`AdminStore.cfg`)."""

    def __init__(self, store: AdminStore) -> None:
        self.s = store

    @property
    def apps(self) -> list[str]:
        return self.s.apps

    async def mutate(self, actor: str, fn: Callable[[dict[str, Any]], tuple[Any, str, str, str, str]]) -> Any:
        """`fn(data)` altera os dados e devolve `(resultado, título, detalhe, ícone, escopo)` da auditoria."""
        async with self.s.locked(True) as data:
            out, title, detail, icon, scope = fn(data)
            event = self.s._push_audit(data, actor, title, detail, icon, scope)
        await self.s.chain.append_shared(event)
        return out

    async def read(self) -> dict[str, Any]:
        async with self.s.locked() as data:
            return data

    # ---- variáveis -------------------------------------------------------------------------
    def _check_bindings(self, raw: Any) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for b in raw or []:
            app, env = str(b.get("app", "")), str(b.get("env", ""))
            if app not in self.apps:
                raise AdminError(f"Aplicação desconhecida: {app}.")
            if env not in dict(APP_ENVS):
                raise AdminError("Ambiente inválido (use staging ou producao).")
            if {"app": app, "env": env} not in out:
                out.append({"app": app, "env": env})
        return sorted(out, key=lambda b: (b["app"], b["env"]))

    def _check_env(self, name: str, i: dict[str, Any], managed: bool) -> dict[str, Any]:
        if not valid_env_name(name):
            raise AdminError("Nome inválido: use letras maiúsculas, números e _ (ex.: MINHA_VARIAVEL).")
        value, kind = str(i.get("value", "")), i.get("kind", "publica")
        if kind not in KINDS:
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        if not value or len(value) > 4096:
            raise AdminError("Informe um valor (até 4096 caracteres).")
        if any(c in value for c in "\0\n\r"):
            raise AdminError("O valor não pode ter quebras de linha nem caracteres nulos.")
        inject = str(i.get("inject_as") or "").strip()
        inject_as = inject if inject and inject != name else None
        if inject_as and not valid_env_name(inject_as):
            raise AdminError("Nome no container inválido (letras maiúsculas, números e _).")
        injected = inject_as or name
        if injected in RESERVED_ENV:
            raise AdminError(f"{injected} é definida pela plataforma.")
        if not managed:
            if injected == INTERNAL_TOKEN_ENV:
                raise AdminError("O token interno é gerado pela plataforma.")
            bad = next((n for n in (name, injected) if is_platform_env(n)), None)
            if bad:
                raise AdminError(
                    f"{bad} é reservada à plataforma (gerida em Aplicações › Conexões e nos tokens dos apps); escolha outro nome."
                )
        var: dict[str, Any] = {
            "name": name,
            "value": value,
            "kind": kind,
            "bindings": self._check_bindings(i.get("bindings")),
        }
        if inject_as:
            var["inject_as"] = inject_as
        var["managed"] = managed
        return var

    def _mutate_env(
        self, d: dict[str, Any], fn: Callable[[], tuple[Any, str, str, str, str, list[str]]]
    ) -> tuple[Any, str, str, str, str]:
        before = [_resolved_map(d["env"], a) for a in self.apps]
        out, title, detail, icon, scope, names = fn()
        for app, prev in zip(self.apps, before, strict=True):
            if _resolved_map(d["env"], app) != prev:
                p = d.setdefault("pending_restart", {}).setdefault(
                    app, {"since": int(time.time() * 1000), "vars": []}
                )
                p["vars"] += [n for n in names if n not in p["vars"]]
        return out, title, detail, icon, scope

    async def env_list(self) -> list[dict[str, Any]]:
        return [masked(v) for v in (await self.read())["env"]]

    async def env_create(self, actor: str, i: dict[str, Any]) -> dict[str, Any]:
        v = self._check_env(str(i.get("name", "")).strip(), i, False)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                if any(e["name"] == v["name"] for e in d["env"]):
                    raise AdminError(f"Já existe uma variável chamada {v['name']}.")
                _conflicts(d["env"], v)
                d["env"].append(v)
                return (
                    masked(v),
                    "Variável de ambiente criada",
                    _detail(v),
                    "variables",
                    _scope(v["bindings"]),
                    [v["name"]],
                )

            return self._mutate_env(d, inner)

        return await self.mutate(actor, run)

    async def env_update(self, actor: str, name: str, i: dict[str, Any]) -> dict[str, Any]:
        requested = self._check_bindings(i.get("bindings"))

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                cur = next((e for e in d["env"] if e["name"] == name), None)
                if cur is None:
                    raise AdminError("Variável não encontrada.", 404)
                data = dict(i)
                if not data.get("value"):
                    data["value"] = cur["value"]
                if cur.get("managed"):
                    if (
                        data.get("kind", "publica") != cur.get("kind", "publica")
                        or data["value"] != cur["value"]
                        or requested != cur.get("bindings", [])
                    ):
                        raise AdminError(
                            "Variável gerida pela plataforma: tipo, valor e vínculos não podem ser alterados (use Rotacionar para trocar o valor)."
                        )
                    data["inject_as"] = cur.get("inject_as")
                v = self._check_env(name, data, bool(cur.get("managed")))
                _conflicts(d["env"], v)
                title = "Variável de ambiente atualizada" + (
                    " (valor alterado)" if cur["value"] != v["value"] else ""
                )
                d["env"][d["env"].index(cur)] = v
                return masked(v), title, _detail(v), "variables", _scope(v["bindings"]), [v["name"]]

            return self._mutate_env(d, inner)

        return await self.mutate(actor, run)

    async def env_delete(self, actor: str, name: str) -> None:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                cur = next((e for e in d["env"] if e["name"] == name), None)
                if cur is None:
                    raise AdminError("Variável não encontrada.", 404)
                if cur.get("managed") and injected_name(cur) == INTERNAL_TOKEN_ENV:
                    raise AdminError("O token interno do app não pode ser excluído.")
                d["env"].remove(cur)
                return (
                    None,
                    "Variável de ambiente excluída",
                    _detail(cur),
                    "trash",
                    _scope(cur.get("bindings", [])),
                    [name],
                )

            return self._mutate_env(d, inner)

        await self.mutate(actor, run)

    async def env_bound(self, app: str) -> list[dict[str, Any]]:
        return [masked(v) for v in (await self.read())["env"] if bound_to(v, app)]

    async def pending_restart(self) -> dict[str, Any]:
        return dict((await self.read()).get("pending_restart", {}))

    async def clear_pending(self, app: str) -> None:
        async with self.s.locked(True) as d:
            d.setdefault("pending_restart", {}).pop(app, None)

    async def managed_var(self, name: str) -> dict[str, Any] | None:
        return next((e for e in (await self.read())["env"] if e.get("managed") and e["name"] == name), None)

    async def upsert_managed(self, actor: str, v: dict[str, Any], title: str) -> bool:
        v = self._check_env(v["name"], v, True)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                same = next((e for e in d["env"] if e["name"] == v["name"]), None)
                if same == v:
                    return False, "", "", "", "", []
                _conflicts(d["env"], v)
                if same is None:
                    d["env"].append(v)
                else:
                    d["env"][d["env"].index(same)] = v
                return True, title, _detail(v), "variables", _scope(v["bindings"]), [v["name"]]

            return self._mutate_env(d, inner)

        async with self.s.locked(True) as d:
            changed, t, detail, icon, scope = run(d)
            event = self.s._push_audit(d, actor, t, detail, icon, scope) if changed else None
        if event:
            await self.s.chain.append_shared(event)
        return bool(changed)

    async def rotate_managed(self, actor: str, name: str, value: str, title: str) -> dict[str, Any]:
        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                slot = next((e for e in d["env"] if e["name"] == name), None)
                if slot is None:
                    raise AdminError("Variável não encontrada.", 404)
                if not slot.get("managed"):
                    raise AdminError("Só variáveis geridas pela plataforma são rotacionadas.")
                if not value or any(c in value for c in "\0\n\r"):
                    raise AdminError("Valor inválido.")
                slot["value"] = value
                return masked(slot), title, _detail(slot), "variables", _scope(slot["bindings"]), [name]

            return self._mutate_env(d, inner)

        return await self.mutate(actor, run)

    async def replace_secret_values(self, actor: str, pairs: dict[str, str]) -> list[str]:
        """Troca senhas antigas (`old → new`) dentro dos valores das variáveis (ex.: DSN com a senha de dev
        depois da rotação do Postgres/MinIO). Idempotente; devolve só os nomes alterados (nunca valores)."""
        pairs = {o: n for o, n in pairs.items() if o and n and o != n}
        if not pairs or not any(o in str(v["value"]) for v in (await self.read())["env"] for o in pairs):
            return []

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            def inner() -> tuple[Any, str, str, str, str, list[str]]:
                names: list[str] = []
                for v in d["env"]:
                    val = str(v["value"])
                    for o, n in pairs.items():
                        val = val.replace(o, n)
                    if val != v["value"]:
                        v["value"] = val
                        names.append(str(v["name"]))
                return (
                    names,
                    "Senhas de infraestrutura rotacionadas",
                    ", ".join(names),
                    "variables",
                    "platform",
                    names,
                )

            return self._mutate_env(d, inner)

        return list(await self.mutate(actor, run))

    async def internal_token_var(self, app: str) -> str | None:
        return next(
            (
                str(v["name"])
                for v in (await self.read())["env"]
                if v.get("managed") and injected_name(v) == INTERNAL_TOKEN_ENV and bound_to(v, app)
            ),
            None,
        )

    async def rotate_internal_token(self, actor: str, app: str) -> dict[str, Any]:
        name = await self.internal_token_var(app)
        if name is None:
            raise AdminError("App sem token interno.", 404)
        return await self.rotate_managed(actor, name, secrets.token_hex(32), "Token interno rotacionado")

    async def ensure_internal_tokens(self, actor: str, seed_of: Callable[[str], str | None]) -> list[str]:
        """Um token interno gerido por app (`HUB_INTERNAL_TOKEN_<APP>`); `seed_of` fixa o valor (demo)."""
        created: list[str] = []
        for app in self.apps:
            fixed = seed_of(app)
            cur = self.s.tokens.get(app)
            if (cur and fixed is None) or (cur and cur == fixed):
                continue
            v = {
                "name": f"{INTERNAL_TOKEN_ENV}_{env_suffix(app)}",
                "value": fixed or secrets.token_hex(32),
                "kind": "secreta",
                "bindings": [{"app": app, "env": e} for e, _ in APP_ENVS],
                "inject_as": INTERNAL_TOKEN_ENV,
            }
            await self.upsert_managed(actor, v, "Token interno do app gerado")
            created.append(app)
        return created

    async def import_bootstrap(self, actor: str, items: list[dict[str, Any]]) -> int:
        """Carga inicial (`HUB_APP_ENV_FILE`): só o que ainda não existe, sem marcar reinício."""
        async with self.s.locked(True) as d:
            n = 0
            for raw in items:
                if any(e["name"] == raw["name"] for e in d["env"]):
                    continue
                # A carga é um arquivo da instalação, não entrada de usuário: um vínculo com app fora do catálogo
                # (ex.: instalar sem os apps de exemplo) descarta o vínculo, nunca derruba o hub no boot.
                if binds := raw.get("bindings"):
                    kept = [b for b in binds if str(b.get("app", "")) in self.apps]
                    if not kept:
                        continue
                    raw = dict(raw) | {"bindings": kept}
                v = self._check_env(str(raw["name"]), raw, True)
                _conflicts(d["env"], v)
                d["env"].append(v)
                n += 1
            event = (
                self.s._push_audit(
                    d,
                    actor,
                    "Variáveis iniciais importadas",
                    f"{n} variáveis da configuração de implantação",
                    "variables",
                    "platform",
                )
                if n
                else None
            )
        if event:
            await self.s.chain.append_shared(event)
        return n

    # ---- funções ---------------------------------------------------------------------------
    @staticmethod
    def _check_role(i: dict[str, Any], name: str) -> None:
        if not name or len(name) > 60:
            raise AdminError("Informe o nome da função (até 60 caracteres).")
        if len(str(i.get("description", ""))) > 200:
            raise AdminError("Descrição muito longa (até 200 caracteres).")
        if i.get("level") not in LEVELS:
            raise AdminError("Nível de acesso inválido.")
        members = i.get("members", 0)
        if not isinstance(members, int) or isinstance(members, bool) or not 0 <= members <= 1_000_000:
            raise AdminError("Quantidade de usuários inválida.")

    @staticmethod
    def _role(d: dict[str, Any], name: str) -> dict[str, Any]:
        r = next((r for r in d["roles"] if r["name"].lower() == name.lower()), None)
        if r is None:
            raise AdminError("Função não encontrada.", 404)
        return r

    async def roles(self) -> list[dict[str, Any]]:
        return list((await self.read())["roles"])

    async def role_create(self, actor: str, i: dict[str, Any]) -> dict[str, Any]:
        name = str(i.get("name", "")).strip()
        self._check_role(i, name)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            if any(r["name"].lower() == name.lower() for r in d["roles"]):
                raise AdminError(f"Já existe uma função chamada {name}.")
            r = {
                "name": name,
                "description": str(i.get("description", "")).strip(),
                "members": i.get("members", 0),
                "level": i["level"],
                "permissions": [False] * len(PERMISSIONS),
            }
            d["roles"].append(r)
            return r, "Função criada", name, "users", "platform"

        return await self.mutate(actor, run)

    async def role_update(self, actor: str, name: str, i: dict[str, Any]) -> dict[str, Any]:
        self._check_role(i, name)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            r = self._role(d, name)
            r.update(
                description=str(i.get("description", "")).strip(),
                members=i.get("members", 0),
                level=i["level"],
            )
            return dict(r), "Função atualizada", name, "users", "platform"

        return await self.mutate(actor, run)

    async def role_delete(self, actor: str, name: str) -> None:
        if name == ADMIN_ROLE:
            raise AdminError("A função Administrador não pode ser excluída.")

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            d["roles"].remove(self._role(d, name))
            return None, "Função excluída", name, "trash", "platform"

        await self.mutate(actor, run)

    async def role_permission(self, actor: str, name: str, index: int, on: bool) -> dict[str, Any]:
        if name == ADMIN_ROLE:
            raise AdminError("As permissões do Administrador não podem ser alteradas.")
        if not 0 <= index < len(PERMISSIONS):
            raise AdminError("Permissão inválida.")

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            r = self._role(d, name)
            r["permissions"] = (list(r.get("permissions", [])) + [False] * len(PERMISSIONS))[
                : len(PERMISSIONS)
            ]
            r["permissions"][index] = on
            verb = "concedida" if on else "revogada"
            detail = f"{name} · {PERMISSIONS[index]} {verb}"
            return dict(r), "Permissões de função alteradas", detail, "users", "platform"

        return await self.mutate(actor, run)

    async def role_apps(self, actor: str, name: str, apps: list[str] | None) -> dict[str, Any]:
        if name == ADMIN_ROLE and apps is not None:
            raise AdminError("O Administrador sempre acessa todas as aplicações.")
        clean: list[str] | None = None
        if apps is not None:
            clean = []
            for a in (str(x).strip() for x in apps):
                if a not in self.apps:
                    raise AdminError(f"Aplicação desconhecida: {a}.")
                if a not in clean:
                    clean.append(a)

        def run(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            r = self._role(d, name)
            if clean is None:
                r.pop("apps", None)
                detail = f"{name} · todas as aplicações"
            else:
                r["apps"] = clean
                detail = f"{name} · {', '.join(clean) or 'nenhuma aplicação'}"
            return dict(r), "Aplicações da função alteradas", detail, "users", "platform"

        return await self.mutate(actor, run)
