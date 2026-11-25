"""Default roles: three levels — Administrador, Power User (everything but managing users) and User (apps only).
Installs seeded before keep their roles; SSO then falls back to the legacy "Colaborador"."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub.admin.config import seed
from hub.auth import _DEMO_USERS
from hub.auth.entra import EntraConfig
from hub.auth.rbac import permissions, roles_for
from hub.routes_login import sso_default_role

NAMES = ["Administrador", "Power User", "User"]


def _perms(role: str, roles: list[dict[str, Any]]) -> list[str]:
    return permissions({"roles": [role]}, roles)[0]


def test_seed_and_fallback_have_the_three_levels(tmp_path: Path) -> None:
    data: dict[str, Any] = {}
    seed(data, [], with_env=False)
    assert [r["name"] for r in data["roles"]] == NAMES
    assert [r["name"] for r in roles_for(tmp_path)] == NAMES


def test_power_user_does_everything_but_manage_users(tmp_path: Path) -> None:
    roles = roles_for(tmp_path)
    power = _perms("Power User", roles)
    assert "gerenciar_usuarios" not in power and len(power) == 5
    assert _perms("User", roles) == ["acessar_aplicacoes"]


def test_demo_users_use_the_new_roles() -> None:
    assert sorted(r for _e, _n, r, _p in _DEMO_USERS) == sorted(NAMES)


def test_sso_default_role_is_user_with_legacy_fallback() -> None:
    sso = EntraConfig(tenant="t", client_id="c", client_secret="s")
    assert sso.default_role == "User"
    assert sso_default_role(sso, [{"name": n} for n in NAMES]) == "User"
    legacy = [{"name": n} for n in ("Administrador", "Tech Lead", "Colaborador")]
    assert sso_default_role(sso, legacy) == "Colaborador"


def test_a_configured_role_that_does_not_exist_falls_back_to_user() -> None:
    sso = EntraConfig(tenant="t", client_id="c", client_secret="s", default_role="Leitor")
    assert sso_default_role(sso, [{"name": n} for n in NAMES]) == "User"  # never a role nobody defined
    assert sso_default_role(EntraConfig(tenant="t", client_id="c", client_secret="s", default_role="Power User"),
                            [{"name": n} for n in NAMES]) == "Power User"  # fmt: skip


def test_first_sso_login_is_never_admin_even_when_configured() -> None:
    """Admin only by explicit assignment (group mapping or a person): a default role that can manage users
    (`HUB_ENTRA_DEFAULT_ROLE=Administrador`, an older install's answer) falls back to "User"."""
    roles = roles_for(Path("/nonexistent"))
    for risky in ("Administrador", "administrador"):
        sso = EntraConfig(tenant="t", client_id="c", client_secret="s", default_role=risky)
        assert sso_default_role(sso, roles) == "User"
    admin_like = [*roles, {"name": "Chefe", "permissions": [True] * 6, "apps": None}]
    sso = EntraConfig(tenant="t", client_id="c", client_secret="s", default_role="Chefe")
    assert sso_default_role(sso, admin_like) == "User"
