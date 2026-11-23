"""Row hover paints the row too: the actions cell is a flex box (shorter than the row), so painting only the cells
left a lighter strip under the Actions column (/admin/users)."""

from __future__ import annotations

from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "../platform/assets/shell/hub.css").read_text("utf-8")


def test_hover_paints_the_whole_row() -> None:
    assert (
        ".data-table tbody tr:hover,.data-table tbody tr:hover td{background:var(--color-surface-hover)}"
        in CSS
    )
