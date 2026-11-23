"""App status labels follow the status-page convention (Statuspage, as used by Anthropic), one word each:
Operational, Degraded (open incident or recent failed probes) and Outage."""

from __future__ import annotations

from hub.ux.texts import t


def test_status_labels_are_the_lean_status_page_set() -> None:
    assert [t(k, "pt-BR") for k in ("status.ok", "status.degraded", "status.unstable", "status.down")] == [
        "Operacional", "Degradado", "Degradado", "Indisponível"]  # fmt: skip
    assert [t(k, "en") for k in ("status.ok", "status.degraded", "status.unstable", "status.down")] == [
        "Operational", "Degraded", "Degraded", "Outage"]  # fmt: skip
