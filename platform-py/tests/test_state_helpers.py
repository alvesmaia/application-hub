import pytest

from hub.state import Change, Config, default_replica_id


def test_config_and_state_helpers(monkeypatch):
    monkeypatch.delenv("HUB_STATE_DB_URL", raising=False)
    assert Config.from_env() is None
    monkeypatch.setenv("HUB_STATE_DB_URL", " postgres://db ")
    monkeypatch.setenv("HUB_STATE_DB_SCHEMA", "INVALID-name")
    monkeypatch.setenv("HUB_STATE_DB_POOL", "bad")
    config = Config.from_env()
    assert config and (config.url, config.schema, config.pool) == ("postgres://db", "hub", 12)
    with pytest.raises(ValueError):
        Config("url", "BadSchema")
    with pytest.raises(ValueError):
        Config("url", pool=0)
    monkeypatch.setenv("HUB_REPLICA_ID", "node / one")
    assert default_replica_id().startswith("nodeone-")
    assert (Change.keep().action, Change.set(1, 2).value, Change.delete().action) == ("keep", 1, "delete")
