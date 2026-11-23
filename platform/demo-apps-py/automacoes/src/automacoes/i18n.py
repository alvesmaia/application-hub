"""Translations (en) of the app's texts: ``i18n.json`` rows ``[pt-BR, en]``, the pt-BR text being the
key of :func:`hub_sdk.locale.t`. Registered when the package is imported."""

from __future__ import annotations

import json
from pathlib import Path

from hub_sdk import locale

CATALOG_FILE = Path(__file__).resolve().parent / "i18n.json"


def load() -> dict[str, dict[str, str]]:
    rows = json.loads(CATALOG_FILE.read_text("utf-8"))
    return {pt: {"en": en} for pt, en in rows}


locale.register(load())
