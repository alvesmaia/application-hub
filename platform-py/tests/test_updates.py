"""New version notice (HUB_UPDATE_CHECK=1) and comparison rules."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from admin_helper import make_hub

from hub import __version__
from hub.ops import updates


def test_newer_only_for_stable_higher_versions() -> None:
    assert updates.newer("0.10.0", "0.9.9") and updates.newer("v1.0.0", "0.2.0")
    assert not updates.newer("0.2.0", "0.2.0") and not updates.newer("0.1.9", "0.2.0")
    assert not updates.newer("0.3.0-rc1", "0.2.0") and not updates.newer("lixo", "0.2.0")


def _client(status: int, body: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(status, json=body)))


def test_check_reads_latest_release_and_ignores_bad_answers() -> None:
    chk = updates.UpdateCheck("dono/repo")
    rel = {"tag_name": "v99.0.0", "html_url": "https://github.com/dono/repo/releases/tag/v99.0.0"}
    asyncio.run(chk.check(_client(200, rel)))
    assert chk.notice() == {"version": "99.0.0", "url": rel["html_url"], "current": __version__}
    asyncio.run(chk.check(_client(200, {"tag_name": "v99.0.0", "html_url": "javascript:alert(1)"})))
    assert chk.notice() is None  # link outside GitHub does not become a notice
    asyncio.run(chk.check(_client(200, {"tag_name": f"v{__version__}", "html_url": rel["html_url"]})))
    assert chk.notice() is None  # already on the latest


def test_health_page_shows_notice(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub(tmp_path, monkeypatch)
    assert "disponível (no ar:" not in hub.get("/admin/health").text
    chk = updates.UpdateCheck("dono/repo")
    chk.latest = {"version": "99.0.0", "url": "https://github.com/dono/repo/releases/tag/v99.0.0"}
    hub.app.state.update_check = chk  # type: ignore[attr-defined]
    page = hub.get("/admin/health").text
    assert (
        "Versão 99.0.0 disponível" in page
        and 'href="https://github.com/dono/repo/releases/tag/v99.0.0"' in page
    )
