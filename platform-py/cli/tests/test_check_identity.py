"""§29: an app with its own colors (`--app-*`) must ship the dark theme too, or it breaks when the user picks Dark."""

from __future__ import annotations

from pathlib import Path

from hub_cli import check


def app_css(tmp_path: Path, css: str) -> Path:
    d = tmp_path / "proj"
    (d / "src" / "pkg" / "assets").mkdir(parents=True)
    (d / "src" / "pkg" / "assets" / "app.css").write_text(css, encoding="utf-8")
    return d


def test_hub_identity_only_is_fine(tmp_path: Path) -> None:
    assert check.step_identity(app_css(tmp_path, ".x { gap: var(--space-8); }")) == (True, "ok")


def test_own_colors_without_dark_theme_fail(tmp_path: Path) -> None:
    ok, detail = check.step_identity(
        app_css(tmp_path, ":root { --app-primary: #0f766e; --app-chrome: #0b3b37; }")
    )
    assert not ok and "§29" in detail and "dark" in detail


def test_own_colors_with_dark_theme_pass(tmp_path: Path) -> None:
    css = ':root { --app-primary: #0f766e; } :root[data-theme="dark"] { --app-primary: #5eead4; }'
    assert check.step_identity(app_css(tmp_path, css)) == (True, "ok")
