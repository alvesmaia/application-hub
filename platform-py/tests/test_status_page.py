"""Status page: the headline follows the apps' states, and the availability history reaches a year."""

import time
from pathlib import Path
from typing import cast

import pytest

from hub.app import create_app
from hub.gateway.registry import Registry
from hub.insight.metrics import Metrics, daily_uptime
from hub.ux.status import status_data


@pytest.fixture
def reg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    return cast(Registry, create_app().state.gateway.registry)


def test_an_unstable_app_is_not_all_systems_operational(reg: Registry, tmp_path: Path) -> None:
    m, now = Metrics(tmp_path), int(time.time())
    assert status_data(reg, None, now, m).overall == "Todos os sistemas operacionais"
    m.record_probe("ativos", "producao", False, now - 30)
    m.record_probe("ativos", "producao", True, now - 20)
    assert status_data(reg, None, now, m).overall == "Algumas aplicações com problemas"


def test_daily_availability_outlives_the_30_days_of_detailed_metrics(tmp_path: Path) -> None:
    now = int(time.time())
    old = now - now % 86_400 - 200 * 86_400 + 3_600  # 200 days ago
    m = Metrics(tmp_path)
    for i in range(4):
        m.record_probe("ativos", "producao", i != 0, old + i * 60)
    m.save()
    (tmp_path / "metrics.json").unlink()  # the detailed buckets of that day are gone (30-day retention)
    days = dict(daily_uptime(Metrics(tmp_path), "ativos", "producao", 365, now))
    assert days[old - old % 86_400] == 0.75
    partial = Metrics(
        tmp_path
    )  # a day leaving the detailed window (fewer samples) never overwrites the full one
    partial.record_probe("ativos", "producao", False, old)
    partial.save()
    (tmp_path / "metrics.json").unlink()
    assert dict(daily_uptime(Metrics(tmp_path), "ativos", "producao", 365, now))[old - old % 86_400] == 0.75


def test_status_page_offers_twelve_months(reg: Registry, tmp_path: Path) -> None:
    m, now = Metrics(tmp_path), int(time.time())
    m.record_probe("ativos", "producao", True, now - 60)
    year = status_data(reg, None, now, m, months=12)
    ativos = next(a for a in year.apps if a.bars[-1][0] == "uptime-bar uptime-ok")
    assert len(ativos.bars) == 12 and ativos.uptime.endswith("em 12 meses")
    assert len(status_data(reg, None, now, m).apps[0].bars) == 30


def test_status_route_switches_the_period(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auth_helper import client_for

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    c = client_for(create_app())
    assert 'href="/status?periodo=12m"' in c.get("/status").text
    page = c.get("/status?periodo=12m").text
    assert "12 meses atrás" in page and 'href="/status"' in page
