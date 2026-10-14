"""Role ("função") operations on `admin.json`: base of `ConfigOps` (`hub/admin/config_env.py`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hub.admin.config import ADMIN_ROLE, LEVELS, PERMISSIONS, AdminError

if TYPE_CHECKING:
    from hub.admin.store import AdminStore


class ConfigBase:
    """Access to `admin.json` with an audited mutation; the operations live in the subclasses."""

    def __init__(self, store: AdminStore) -> None:
        self.s = store

    @property
    def apps(self) -> list[str]:
        return self.s.apps

    async def mutate(self, actor: str, fn: Callable[[dict[str, Any]], tuple[Any, str, str, str, str]]) -> Any:
        """`fn(data)` changes the data and returns `(result, title, detail, icon, scope)` for the audit."""
        async with self.s.locked(True) as data:
            out, title, detail, icon, scope = fn(data)
            event = self.s._push_audit(data, actor, title, detail, icon, scope)
        await self.s.chain.append_shared(event)
        return out

    async def read(self) -> dict[str, Any]:
        async with self.s.locked() as data:
            return data


class RoleOps(ConfigBase):
    # ---- functions ----------------------------------------------------------------------
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
