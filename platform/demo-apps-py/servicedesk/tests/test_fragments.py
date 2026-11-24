"""Static fragments: every action button gives a visible and truthful result (no fake success)."""

from __future__ import annotations

import html
import re
from pathlib import Path

from servicedesk import i18n
from servicedesk.i18n import HERE

DEMO = "Demonstração: ação desabilitada."
# Honest toasts that stay: the help card (shared with the SDK workspace).
KEEP = {DEMO, "O suporte entrará em contato em breve.", "Filtros disponíveis em breve."}
FRAGMENTS = sorted((Path(HERE) / "fragments").glob("*.html"))


def test_toasts_never_claim_an_effect_that_did_not_happen() -> None:
    toasts = {t for f in FRAGMENTS for t in re.findall(r'data-hub-toast="([^"]*)"', f.read_text("utf-8"))}
    assert toasts - KEEP == set()


def test_every_button_has_a_behavior() -> None:
    idle = [
        (f.name, b)
        for f in FRAGMENTS
        for b in re.findall(r"<button[^>]*>", f.read_text("utf-8"))
        if not re.search(r'data-hub-|data-tsc-|type="submit"|class="(selected|current)"', b)
    ]
    assert idle == []


def test_new_request_opens_the_real_create_form() -> None:
    for name in ("dashboard", "requests"):
        src = (Path(HERE) / "fragments" / f"{name}.html").read_text("utf-8")
        assert 'href="{{ base }}/requests?new=1"' in src, name


# Sample data of the mockup (names, codes, places): passes through untranslated on purpose.
DATA = {"Mariana Costa", "Rafael Mendes", "Camila Rocha", "Lucas Ferreira", "Ana Beatriz", "Pedro Alves"}
CODE = re.compile(r"(SD|APR)-\d{4}-\d{4,7}")  # ticket protocols and approval numbers
ATTRS = re.compile(r'(?:placeholder|title|aria-label|data-hub-toast|alt)="([^"]*)"')


def visible_texts(src: str) -> set[str]:
    """What the language middleware translates: text between tags and some attributes."""
    src = re.sub(r"<(svg|style|script|textarea)\b.*?</\1>", "", src, flags=re.S)
    found = set(ATTRS.findall(src)) | set(re.split(r"<[^>]*>", src))
    return {html.unescape(t.strip()) for t in found}


def test_every_visible_text_has_an_english_entry() -> None:
    catalog = i18n.catalog()
    missing = sorted(
        t
        for f in FRAGMENTS
        for t in visible_texts(f.read_text("utf-8"))
        if re.search(r"[A-Za-zÀ-ú]{2}", t) and "{{" not in t and t not in catalog
        if t not in DATA and not CODE.fullmatch(t)
    )
    assert missing == []
