"""Adapting an existing app: `hub check` holds the port to the original CSS (copied untouched, the hub's
changes in one overrides file) and to a finished inventory of routes and behaviors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub_cli import port
from hub_cli.errors import CliError
from hub_cli.main import main

DONE = """# Inventory
## Routes
- [x] `/tickets` list with header filters
## Behaviors
- [x] board drag and drop → data-hub-drag
- [~] combobox with search — no kit equivalent yet; <details> list
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    orig = tmp_path / "original" / "static"
    orig.mkdir(parents=True)
    (orig / "base.css").write_text(".a{color:red}", encoding="utf-8")
    (orig / "theme.css").write_text(":root{--x:1}", encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / "assets" / "original").mkdir(parents=True)
    for name in ("base.css", "theme.css"):
        (proj / "assets" / "original" / name).write_bytes((orig / name).read_bytes())
    (proj / "assets" / "hub-overrides.css").write_text(".hub{x:y}", encoding="utf-8")
    cfg = {
        "original": "../original",
        "css": [
            ["static/base.css", "assets/original/base.css"],
            ["static/theme.css", "assets/original/theme.css"],
        ],
        "app_css": "assets/app.css",
        "overrides": "assets/hub-overrides.css",
    }
    (proj / "port.json").write_text(json.dumps(cfg), encoding="utf-8")
    (proj / "port-inventory.md").write_text(DONE, encoding="utf-8")
    port.write_css(proj)
    return proj


def test_not_a_port_passes() -> None:
    ok, out = port.step_port(Path("/nonexistent-project"))
    assert ok and "port.json" in out


def test_untouched_copies_built_css_and_done_inventory_pass(project: Path) -> None:
    assert (project / "assets" / "app.css").read_text(
        encoding="utf-8"
    ) == ".a{color:red}\n:root{--x:1}\n.hub{x:y}"
    assert port.step_port(project) == (True, "CSS original intacto; inventário concluído")


def test_an_edited_copy_fails_and_points_to_the_overrides(project: Path) -> None:
    (project / "assets" / "original" / "theme.css").write_text(":root{--x:2}", encoding="utf-8")
    ok, out = port.step_port(project)
    assert not ok and "assets/original/theme.css difere do original" in out and "hub-overrides.css" in out


def test_app_css_edited_by_hand_fails(project: Path) -> None:
    (project / "assets" / "app.css").write_text(".a{color:blue}", encoding="utf-8")
    ok, out = port.step_port(project)
    assert not ok and "hub port css" in out


def test_without_the_original_folder_only_the_build_is_checked(project: Path) -> None:
    cfg = json.loads((project / "port.json").read_text(encoding="utf-8"))
    (project / "port.json").write_text(json.dumps({**cfg, "original": "../gone"}), encoding="utf-8")
    assert port.step_port(project)[0]


def test_inventory_pending_items_and_deviations_without_reason_fail(project: Path) -> None:
    (project / "port-inventory.md").write_text(
        "- [x] `/a`\n- [ ] `/wiki/new` editor\n- [~] multi-select assignees\n", encoding="utf-8"
    )
    ok, out = port.step_port(project)
    assert not ok
    assert "pendente: `/wiki/new` editor" in out
    assert "desvio sem motivo (`— motivo`): multi-select assignees" in out


def test_missing_inventory_fails(project: Path) -> None:
    (project / "port-inventory.md").unlink()
    ok, out = port.step_port(project)
    assert not ok and "port-inventory.md ausente" in out


def test_invalid_config_says_the_expected_shape(project: Path) -> None:
    (project / "port.json").write_text('{"css": []}', encoding="utf-8")
    with pytest.raises(CliError, match="app_css"):
        port.step_port(project)


def test_command_line_writes_the_css(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "assets" / "hub-overrides.css").write_text(".hub{z:w}", encoding="utf-8")
    assert main(["port", "css", "--dir", str(project)]) == 0
    assert (project / "assets" / "app.css").read_text(encoding="utf-8").endswith(".hub{z:w}")
    assert "app.css" in capsys.readouterr().out
