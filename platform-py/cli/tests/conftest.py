"""Every test gets its own CLI config dir: the developer's real `hub login` is never read nor overwritten."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for v in ("HUB_URL", "HUB_TOKEN", "HUB_TOKEN_FILE"):
        monkeypatch.delenv(v, raising=False)
