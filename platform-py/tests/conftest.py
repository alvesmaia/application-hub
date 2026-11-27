"""Paths to the assets and the app registry (moved out of `platform/crates`/`platform/apps/hub-web`)."""

import os
import tempfile
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[2] / "platform"
os.environ.setdefault(
    "HUB_ASSET_DIRS", os.pathsep.join(str(_PLATFORM / "assets" / d) for d in ("shell", "web"))
)
os.environ.setdefault("HUB_APPS_FILE", str(_PLATFORM / "config" / "apps.json"))
# isolated per session by default: a test that does not set the folder does not write to `platform/data` (two pytest runs at once
# would break the shared audit chain); the helpers still switch to `tmp_path` per test
os.environ.setdefault("HUB_DATA_DIR", tempfile.mkdtemp(prefix="hub-test-"))
# a running instance's address exported in the shell (e.g. `. .iso-instance.env` before E2E) must not leak in:
# the SSO callback URL is built from it, and every Entra flow failed with "Sessão de login expirada"
os.environ.pop("HUB_PUBLIC_URL", None)
