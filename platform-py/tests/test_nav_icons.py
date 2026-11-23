"""Menu icons: Status shows the gauge (it is about how the apps are doing); the Operação group (tasks, backups,
health, logs) gets a wrench; no two entries of the menu share an icon."""

from __future__ import annotations

import json
from pathlib import Path

NAV = json.loads((Path(__file__).resolve().parents[1] / "hub/web/nav.json").read_text("utf-8"))
GAUGE = '<path d="M4.2 18a9 9 0 1 1 15.6 0"/><path d="m12 14 4-5"/><circle cx="12" cy="14" r="1.5"/>'


def test_status_uses_the_gauge_and_operations_the_wrench() -> None:
    status = next(i for i in NAV["top"] if i["href"] == "/status")
    ops = next(g for g in NAV["groups"] if g["id"] == "ops")
    assert status["icon"] == GAUGE
    assert ops["icon"] != GAUGE and "M14.7 6.3" in ops["icon"]  # wrench


def test_menu_icons_are_unique() -> None:
    icons = [i["icon"] for i in NAV["top"]] + [g["icon"] for g in NAV["groups"]]
    icons += [i["icon"] for g in NAV["groups"] for i in g["items"]]
    assert len(icons) == len(set(icons))
