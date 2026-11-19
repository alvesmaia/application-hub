"""In English, no screen leaks Portuguese: page chrome, hidden texts revealed by JS (e.g. "Prompt copied") and
attributes. The /developers docs body is Portuguese by design (`<!--notr-->`); user data (app and user names) too."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient

from hub.ops import devdocs

WEB = Path(__file__).resolve().parents[1] / "hub" / "web"
SKIP = re.compile(
    r"<(script|style|code|pre|textarea|select)\b.*?</\1>|<!--notr-->.*?<!--/notr-->", re.S | re.I
)
TEXT = re.compile(
    r">([^<>]*\w[^<>]*)<|\s(?:placeholder|aria-label|title|alt|data-label|data-confirm)=\"([^\"]+)\""
)
PT = re.compile(
    r"[ãõçâêôáíú]|\b(?:não|para|você|sem|com|de|(?-i:do)(?! not)|da|dos|das|em|ao|nenhum|nenhuma)\b", re.I
)
# user data, not interface: names of the sample apps and users, the native name of a language, places
DATA = re.compile(r"Gestão de Ativos|Central de Serviços|Português|São Paulo|Ação|Demo|@demo\.local", re.I)


def _routes() -> list[str]:
    nav = json.loads((WEB / "nav.json").read_text("utf-8"))
    out: set[str] = {"/", "/account", "/account/security", "/account/access", "/inbox", "/search?q=x"}

    def walk(items: list[dict[str, Any]]) -> None:
        for it in items:
            out.update([it["href"], *it.get("also", [])] if "href" in it else [])
            walk(it.get("children", []) + it.get("items", []))

    walk(nav if isinstance(nav, list) else nav.get("items", []))
    return sorted(r for r in out if not r.startswith("http"))


def leaks(html: str) -> list[str]:
    body = SKIP.sub("", html.split("<body", 1)[-1])
    found = {" ".join((a or b).split()) for a, b in TEXT.findall(body)}
    return sorted(s for s in found if PT.search(s) and not DATA.search(s))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_signed_in_pages_have_no_portuguese_in_english(hub: TestClient) -> None:
    bad = {}
    for r in _routes():
        resp = hub.get(r, headers={"accept-language": "en"})
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            bad[r] = leaks(resp.text)
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=1)


def test_developers_has_no_portuguese_chrome_in_english(hub: TestClient) -> None:
    anon, bad = TestClient(hub.app), {}
    for d in devdocs.all_docs():
        path = "/developers" + (f"/{d.slug}" if d.slug else "")
        bad[path] = leaks(anon.get(path, headers={"accept-language": "en"}).text)
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=1)


def test_forbidden_page_and_pager_count_follow_the_language(hub: TestClient) -> None:
    from admin_helper import add_user

    page = add_user(hub, "leitor", ["Colaborador"]).get("/admin/users", headers={"accept-language": "en"})
    assert page.status_code == 403 and "You do not have permission to access this page." in page.text
    assert not leaks(page.text)
