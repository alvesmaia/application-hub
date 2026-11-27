"""Tasks screen: the duration reads like "20m3s" (under a minute, only "57s"); attempts live in the side panel,
not in a table column."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.ops.tasks_view import duration_label


@pytest.mark.parametrize(
    ("seconds", "label"),
    [(0, "0s"), (0.4, "0s"), (57, "57s"), (59.6, "1m0s"), (60, "1m0s"), (1203, "20m3s"), (3725, "1h2m5s")],
)
def test_duration_label(seconds: float, label: str) -> None:
    assert duration_label(seconds) == label


def test_the_table_has_no_attempts_column_but_the_panel_keeps_it() -> None:
    page = (Path(__file__).parents[1] / "hub/web/templates/ops_tasks.html").read_text(encoding="utf-8")
    thead = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert "Tentativas" not in thead and "col-tent" not in page
    assert "<dt>Tentativas</dt>" in page
