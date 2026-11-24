"""Administração › Saúde, Serviços: every app of the registry is monitored (the seed came from the mockup and
missed the collector and automations); an app added later shows up once, a service the admin removed stays removed."""

from __future__ import annotations

from typing import Any

from hub.insight.ops import seed_health

NAMES = {"inventory": "Gestão de Ativos", "collector": "Coletor", "automations": "Automações"}


def test_a_fresh_install_lists_every_app_by_its_registry_name() -> None:
    d: dict[str, Any] = {}
    seed_health(d, NAMES, 0)
    names = [s["name"] for s in d["health"]]
    assert names == ["Autenticação", "Gestão de Ativos", "Coletor", "Automações", "Notificações"]
    assert all(s["checked_at"] is None for s in d["health"])  # never checked yet: no made-up "2 min ago"


def test_an_install_seeded_before_gets_the_missing_apps_once() -> None:
    old = {"name": "Gestão de Ativos", "kind": "Aplicação", "status": "Disponível", "app": "inventory"}
    d: dict[str, Any] = {"health": [old], "health_seeded": True}
    seed_health(d, NAMES, 0)
    assert [s["app"] for s in d["health"]] == ["inventory", "collector", "automations"]
    d["health"] = [s for s in d["health"] if s["app"] != "collector"]  # the admin removes it
    seed_health(d, NAMES, 0)
    assert "collector" not in [s["app"] for s in d["health"]]
