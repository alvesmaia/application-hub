"""`hub compare`: the same routes of the original app and of the app inside `hub dev`, side by side, with a
style difference per page (fonts, palette, radii, how many buttons/fields/rows/links/icons); fails while any
page differs more than the limit (adapting must keep the look, not re-create it)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hub_cli import compare
from hub_cli.errors import CliError
from hub_cli.main import main

SAME: dict[str, Any] = {
    "fonts": {"inter": 900, "material symbols rounded": 100},
    "palette": {"#103050": 50, "#f0f0f0": 50},
    "radii": {"8": 10},
    "counts": {"button": 10, "field": 2, "row": 20, "link": 30, "heading": 2, "icon": 12},
}
REWRITTEN: dict[str, Any] = {
    "fonts": {"nunito sans": 1000},
    "palette": {"#103050": 20, "#ffffff": 80},
    "radii": {"12": 10},
    "counts": {"button": 2, "field": 2, "row": 20, "link": 20, "heading": 1, "icon": 2},
}


class FakeBrowser:
    """Named fake of the browser side: records what was shot and answers a fixed style per side and route."""

    def __init__(self, hub_styles: dict[str, dict[str, Any]]) -> None:
        self.hub_styles = hub_styles
        self.shots: list[tuple[str, str]] = []
        self.logins: list[tuple[str, str]] = []

    def login(self, url: str, user: str, password: str) -> None:
        self.logins.append((url, user))

    def shoot(self, url: str, path: Path, selector: str, hub: bool) -> dict[str, Any]:
        self.shots.append((url, selector))
        path.write_bytes(b"PNG fake")
        if not hub:
            return SAME
        return self.hub_styles[compare.slug(url.split("/frota", 1)[1])]


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "manifest.json").write_text(json.dumps({"id": "frota"}), encoding="utf-8")
    return tmp_path


def _run(project_dir: Path, fake: FakeBrowser, **kw: Any) -> int:
    opts = compare.Options(
        original="http://127.0.0.1:9000", routes=["/", "/veiculos"], directory=project_dir, **kw
    )
    return compare.run(opts, fake)


def test_identical_style_is_zero_and_a_rewrite_is_far() -> None:
    assert compare.difference(SAME, SAME)["total"] == 0.0
    far = compare.difference(SAME, REWRITTEN)
    assert far["total"] > 50 and far["fonts"] > 50 and far["radii"] == 100.0


def test_difference_explains_what_changed() -> None:
    lines = compare.explain(SAME, REWRITTEN)
    assert any("material symbols rounded" in ln for ln in lines)  # a font of the original is missing
    assert any("button" in ln and "10" in ln and "2" in ln for ln in lines)


def test_same_routes_on_both_sides_and_a_report(project_dir: Path) -> None:
    fake = FakeBrowser({"root": SAME, "veiculos": SAME})
    assert _run(project_dir, fake) == 0
    assert fake.shots == [
        ("http://127.0.0.1:9000/", "body"),
        ("http://127.0.0.1:4173/apps/frota/", "#app-root"),
        ("http://127.0.0.1:9000/veiculos", "body"),
        ("http://127.0.0.1:4173/apps/frota/veiculos", "#app-root"),
    ]
    report = (project_dir / ".hub" / "compare" / "index.html").read_text(encoding="utf-8")
    assert "0.0%" in report and 'src="original--veiculos.png"' in report


def test_fails_while_a_page_differs_more_than_the_limit(
    project_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(project_dir, FakeBrowser({"root": SAME, "veiculos": REWRITTEN}), max_diff=25.0) == 1
    out = capsys.readouterr().out
    assert "FALHOU" in out and "/veiculos" in out and "25" in out and "material symbols rounded" in out


def test_login_on_the_original_uses_the_environment(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBrowser({"root": SAME, "veiculos": SAME})
    monkeypatch.delenv("HUB_COMPARE_USER", raising=False)
    with pytest.raises(CliError, match="HUB_COMPARE_USER"):
        _run(project_dir, fake, login="/login")
    monkeypatch.setenv("HUB_COMPARE_USER", "ana")
    monkeypatch.setenv("HUB_COMPARE_PASSWORD", "x")
    assert _run(project_dir, fake, login="/login") == 0
    assert fake.logins == [("http://127.0.0.1:9000/login", "ana")]


def test_selectors_and_hub_address_can_change(project_dir: Path) -> None:
    fake = FakeBrowser({"root": SAME, "veiculos": SAME})
    _run(project_dir, fake, hub="http://127.0.0.1:5000/", original_selector="main")
    assert fake.shots[0] == ("http://127.0.0.1:9000/", "main")
    assert fake.shots[1] == ("http://127.0.0.1:5000/apps/frota/", "#app-root")


def test_route_names_for_files() -> None:
    assert compare.slug("/") == "root"
    assert compare.slug("/veiculos/12/editar?x=1") == "veiculos-12-editar-x-1"


def test_without_playwright_the_error_says_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compare, "_playwright", lambda: None)
    with pytest.raises(CliError, match="--with playwright"):
        compare.real_browser()


def test_command_line(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[compare.Options] = []
    monkeypatch.setattr(compare, "real_browser", lambda: FakeBrowser({}))
    monkeypatch.setattr(compare, "run", lambda o, b: seen.append(o) or 0)
    rc = main(
        [
            "compare",
            "http://127.0.0.1:9000",
            "--routes",
            "/",
            "/x",
            "--dir",
            str(project_dir),
            "--max-diff",
            "10",
        ]
    )
    assert rc == 0 and seen[0].routes == ["/", "/x"] and seen[0].max_diff == 10.0
