"""Administration/Operations screens follow the user's language (tr_html on render)."""

from __future__ import annotations

from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient

from hub.ux.texts import tr_html


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("lang", "new", "title"),
    [("pt-BR", "Novo usuário", "Usuários"), ("en", "New user", "Users"), ("es", "New user", "Users")],
)
def test_admin_users_per_language(hub: TestClient, lang: str, new: str, title: str) -> None:
    html = hub.get("/admin/users", headers={"accept-language": lang}).text
    assert new in html and f'<h1 class="page-title">{title}</h1>' in html


@pytest.mark.parametrize(("lang", "word"), [("en", "Application"), ("es", "Application")])
def test_ops_page_per_language(hub: TestClient, lang: str, word: str) -> None:
    assert word in hub.get("/admin/tasks", headers={"accept-language": lang}).text


def test_tr_html_skips_code_attrs_and_notr() -> None:
    src = '<p title="Usuários">Novo usuário <code>Novo usuário</code></p><!--notr-->Entrar<!--/notr-->'
    assert (
        tr_html(src, "en")
        == '<p title="Users">New user <code>Novo usuário</code></p><!--notr-->Entrar<!--/notr-->'
    )
    assert tr_html(src, "pt-BR") == src


def test_preformatted_text_is_not_translated() -> None:
    # deploy and build logs (<pre>): thousands of lines, none in the catalog; matching each one against every
    # template of the catalog froze the whole hub for seconds on the Deployments page in English
    assert tr_html("<p>Usuários</p><pre>Usuários</pre>", "en") == "<p>Users</p><pre>Usuários</pre>"
    assert tr_html('<pre title="Usuários">x</pre>', "en") == '<pre title="Users">x</pre>'


def test_long_texts_out_of_the_catalog_translate_fast() -> None:
    # every miss used to run ~500 lazy regexes over the whole text (12 ms at 4,000 characters); the literal parts
    # of each template are checked first
    import time

    from hub.ux.texts import tr

    texts = [f"linha {i} " + "palavra qualquer " * 240 for i in range(50)]
    t0 = time.perf_counter()
    assert [tr(s, "en") for s in texts] == texts
    assert time.perf_counter() - t0 < 0.25


def test_a_template_with_a_long_value_still_translates() -> None:
    from hub.ux.texts import _catalog, tr

    head = next(
        r for _, r, lits in _catalog()[1] if r[0].endswith("}") and len(lits) == 1 and len(lits[0]) > 6
    )
    value = "x" * 3000
    filled = head[0].split("{")[0] + value
    assert tr(filled, "en").endswith(value) and tr(filled, "en") != filled
