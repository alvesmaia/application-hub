"""One name per app on every hub screen: the display name of the registry (the short label of `apps.json` made the
same app read "Serviços"/"Services" in Administration and "Central de Serviços" in the catalog)."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub


def test_admin_and_insight_use_the_display_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    reg = hub.app.state.gateway.registry
    for e in reg.entries():
        e.display["short"] = "CURTO-" + e.id  # a short label that must not show up anywhere
    for page in ("/admin/env", "/admin/logs", "/admin/audit", "/admin/health", "/admin/tokens", "/status"):
        r = hub.get(page)
        assert r.status_code == 200, page
        assert "CURTO-" not in r.text, page
