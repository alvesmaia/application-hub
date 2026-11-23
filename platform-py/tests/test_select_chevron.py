"""The select arrow is one SVG chevron (per theme), not two gradient half-squares: at some zoom levels and screen
densities those rendered as two separate triangles."""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "../platform/assets/shell/hub.css").read_text("utf-8")


def _select_rule() -> str:
    m = re.search(r"\nselect:not\(\[multiple\]\),\.hub-select\{([^}]*)\}", CSS)
    assert m
    return str(m.group(1))


def test_select_uses_a_single_chevron() -> None:
    rule = _select_rule()
    assert "linear-gradient" not in rule
    assert "background-image:var(--hub-select-chevron)!important" in rule


def test_chevron_has_light_and_dark_colors() -> None:
    defs = re.findall(
        r"--hub-select-chevron:url\(\"data:image/svg\+xml,[^\"]*stroke='%23([0-9A-Fa-f]{6})'", CSS
    )
    assert sorted(set(defs)) == ["52606D", "A5A5A5"], defs
    assert CSS.count("--hub-select-chevron:url(") == 3  # light, dark and auto-dark
