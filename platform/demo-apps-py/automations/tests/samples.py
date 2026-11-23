"""Helpers to reach the sample packages shipped in ``src/automations/samples``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from automations.package_format import PackageManifest

SAMPLES = Path(__file__).resolve().parents[1] / "src" / "automations" / "samples"


def manifest(name: str) -> PackageManifest:
    return PackageManifest.model_validate_json((SAMPLES / name / "automation.json").read_text("utf-8"))


def sample_module(package: str, module: str) -> ModuleType:
    """Imports ``samples/<package>/<module>.py`` as ``<package>_<module>`` (siblings importable)."""
    folder = SAMPLES / package
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(
        f"{package.replace('-', '_')}_{module}", folder / f"{module}.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod
