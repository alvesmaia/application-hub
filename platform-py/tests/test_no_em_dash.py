"""The owner does not want em dashes in UI text: the catalog (both languages) and the templates."""

import json
from pathlib import Path

WEB = Path(__file__).parents[1] / "hub/web"
EM_DASH = "—"


def test_catalog_has_no_em_dash() -> None:
    rows = json.loads((WEB / "catalog.json").read_text(encoding="utf-8"))
    bad = [r for r in rows if any(EM_DASH in s for s in r)]
    assert bad == []


def test_templates_have_no_em_dash() -> None:
    bad = [f.name for f in (WEB / "templates").glob("*.html") if EM_DASH in f.read_text(encoding="utf-8")]
    assert bad == []
