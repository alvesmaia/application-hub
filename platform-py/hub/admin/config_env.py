"""Environment variable operations on `admin.json`: `ConfigOps` = variables + roles (`AdminStore.cfg`)."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from hub.admin.config import (
    APP_ENVS,
    INTERNAL_TOKEN_ENV,
    KIND_LABEL,
    KINDS,
    RESERVED_ENV,
    AdminError,
    bound_to,
    env_label,
    env_suffix,
    injected_name,
    is_platform_env,
    masked,
    resolve,
    valid_env_name,
)
from hub.admin.config_roles import RoleOps


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


def _check_value(value: str, kind: Any) -> None:
    if kind not in KINDS:
        raise AdminError("Dados inválidos: verifique os campos enviados.")
    if not value or len(value) > 4096:
        raise AdminError("Informe um valor (até 4096 caracteres).")
    if any(c in value for c in "\0\n\r"):
        raise AdminError("O valor não pode ter quebras de linha nem caracteres nulos.")


def _check_reserved(name: str, injected: str, managed: bool) -> None:
    if injected in RESERVED_ENV:
        raise AdminError(f"{injected} é definida pela plataforma.")
    if managed:
        return
    if injected == INTERNAL_TOKEN_ENV:
        raise AdminError("O token interno é gerado pela plataforma.")
    bad = next((n for n in (name, injected) if is_platform_env(n)), None)
    if bad:
        raise AdminError(
            f"{bad} é reservada à plataforma (gerida em Aplicações › Conexões e nos tokens dos apps); escolha outro nome."
        )


class ConfigOps(RoleOps):
    """Variable and function operations on `admin.json` (`AdminStore.cfg`)."""

    # ---- variables ----------------------------------------------------------------------
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
        _check_value(value, kind)
        inject = str(i.get("inject_as") or "").strip()
        inject_as = inject if inject and inject != name else None
        if inject_as and not valid_env_name(inject_as):
            raise AdminError("Nome no container inválido (letras maiúsculas, números e _).")
        _check_reserved(name, inject_as or name, managed)
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
        """Replaces old passwords (`old → new`) inside variable values (e.g. a DSN with the dev password
        after the Postgres/MinIO rotation). Idempotent; returns only the names changed (never values)."""
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
        """An internal token managed per app (`HUB_INTERNAL_TOKEN_<APP>`); `seed_of` fixes the value (demo)."""
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
        """Initial load (`HUB_APP_ENV_FILE`): only what does not exist yet, without marking a restart."""
        async with self.s.locked(True) as d:
            n = 0
            for raw in items:
                if any(e["name"] == raw["name"] for e in d["env"]):
                    continue
                # The load is an installation file, not user input: a binding to an app outside the catalog
                # (e.g. installing without the sample apps) drops the binding; it never brings the hub down at boot.
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
