"""Per-app limits in the shared Postgres: a runaway query becomes an error in its own app, not a stalled hub."""

from __future__ import annotations

import pytest

from hub.ops import provision as pv
from hub.ops.model import OpsError


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("HUB_APP_DB_STATEMENT_TIMEOUT", "HUB_APP_DB_IDLE_TX_TIMEOUT", "HUB_APP_DB_TEMP_FILE_LIMIT"):
        monkeypatch.delenv(k, raising=False)


def test_each_app_role_gets_the_default_limits() -> None:
    sql = pv.limits_sql("app_x")
    assert "ALTER ROLE \"app_x\" SET statement_timeout = '5min'" in sql
    assert "ALTER ROLE \"app_x\" SET idle_in_transaction_session_timeout = '5min'" in sql
    assert "ALTER ROLE \"app_x\" SET temp_file_limit = '5GB'" in sql


def test_the_operator_can_change_a_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_APP_DB_STATEMENT_TIMEOUT", "30s")
    monkeypatch.setenv("HUB_APP_DB_TEMP_FILE_LIMIT", "-1")  # -1 = no limit
    sql = pv.limits_sql("app_x")
    assert "statement_timeout = '30s'" in " ".join(sql) and "temp_file_limit = '-1'" in " ".join(sql)


def test_a_malformed_limit_is_refused_with_the_expected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_APP_DB_STATEMENT_TIMEOUT", "5min'; DROP DATABASE hub; --")
    with pytest.raises(OpsError, match=r"HUB_APP_DB_STATEMENT_TIMEOUT.*5min"):
        pv.limits_sql("app_x")


def test_provisioning_applies_the_limits() -> None:
    ddl = pv._ddl("app_x", "hub-managed:app=x", "p" * 48, None, None)
    assert any("statement_timeout" in s for s in ddl)
