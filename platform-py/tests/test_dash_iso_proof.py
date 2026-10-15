"""scripts/dash-iso-proof.py: an unknown command prints the usage instead of silently running `check`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dash-iso-proof.py"


def test_unknown_command_prints_usage_and_exits_2() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "bind"], capture_output=True, text=True, timeout=30)  # noqa: S603
    assert r.returncode == 2
    assert "setup|link|check" in r.stderr
