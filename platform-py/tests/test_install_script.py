"""`scripts/install.sh`: options with a value are read (regression: `shiftASKED=1` without `;`)."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


@pytest.mark.skipif(not shutil.which("bash"), reason="sem bash")
def test_value_options_are_parsed() -> None:
    opts = ["--admin-email", "a@b.test", "--public-url", "http://x", "--port", "8095", "--bind", "127.0.0.1"]
    opts += ["--project", "app-hub-x", "--subnet", "10.9", "--no-demo-apps", "--help"]
    r = subprocess.run(  # noqa: S603
        ["bash", SCRIPT.name, *opts],  # noqa: S607
        cwd=SCRIPT.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "opção desconhecida" not in r.stderr
