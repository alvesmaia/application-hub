"""The installed wheel carries every file of the sample packages (not only the .py): without their
automation.json the first start in the container failed to import them."""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "automations"


def test_package_data_ships_every_sample_file() -> None:
    patterns = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["setuptools"][
        "package-data"
    ]
    shipped = patterns["automations"]
    files = [p.relative_to(PKG).as_posix() for p in (PKG / "samples").rglob("*") if p.is_file()]
    files = [f for f in files if "__pycache__" not in f and not f.endswith(".py")]
    assert files, "samples without data files?"
    missing = [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in shipped)]
    assert not missing, missing
