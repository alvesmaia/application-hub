"""Host capacity bars on /admin/runtime: system, platform and apps must not share a color, or the bar reads as one
block. `--color-accent` and `--color-info` both resolve to `--am-primary`, so a token name alone proves nothing."""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "platform" / "assets"


def _tokens() -> dict[str, str]:
    """First (light theme) definition of every custom property in the shell tokens."""
    found: dict[str, str] = {}
    for name, value in re.findall(
        r"(--[\w-]+):\s*([^;]+);", (ASSETS / "shell" / "tokens.css").read_text("utf-8")
    ):
        found.setdefault(name, value.strip())
    return found


def _resolve(value: str, tokens: dict[str, str]) -> str:
    while m := re.fullmatch(r"var\((--[\w-]+)(?:,[^)]*)?\)", value):
        value = tokens[m.group(1)]
    return value.lower()


def test_platform_and_apps_segments_have_distinct_colors() -> None:
    css, tokens = (ASSETS / "web" / "ops.css").read_text("utf-8"), _tokens()
    seg = {k: re.search(rf"\.ops-seg\.{k}\{{background:([^}}]+)\}}", css) for k in ("sys", "plat", "apps")}
    colors = {k: _resolve(m.group(1), tokens) for k, m in seg.items() if m}
    assert len(colors) == 3 and len(set(colors.values())) == 3, colors


def test_apps_segment_is_magenta_not_green() -> None:
    """Green read as "free"; magenta stands apart from the platform's blue and carries no status meaning."""
    css = (ASSETS / "web" / "ops.css").read_text("utf-8")
    assert (
        ".ops-seg.apps{background:#d6409f}" in css.lower()
        and ".ops-legend .apps:before{background:#d6409f}" in css.lower()
    )
