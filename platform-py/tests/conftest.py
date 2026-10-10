"""Caminhos dos assets e do registro de apps (saíram de `platform/crates`/`platform/apps/hub-web`)."""

import os
import tempfile
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[2] / "platform"
os.environ.setdefault(
    "HUB_ASSET_DIRS", os.pathsep.join(str(_PLATFORM / "assets" / d) for d in ("shell", "web"))
)
os.environ.setdefault("HUB_APPS_FILE", str(_PLATFORM / "config" / "apps.json"))
# padrão isolado por sessão: teste que não define a pasta não grava em `platform/data` (dois pytest juntos
# quebravam a cadeia de auditoria compartilhada); os helpers ainda trocam por `tmp_path` por teste
os.environ.setdefault("HUB_DATA_DIR", tempfile.mkdtemp(prefix="hub-test-"))
