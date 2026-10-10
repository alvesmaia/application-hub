"""Telas de Administração/Operação seguem o idioma do usuário (tr_html no render)."""

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
    [("pt-BR", "Novo usuário", "Usuários"), ("en", "New user", "Users"), ("es", "Nuevo usuario", "Usuarios")],
)
def test_admin_users_per_language(hub: TestClient, lang: str, new: str, title: str) -> None:
    html = hub.get("/admin/users", headers={"accept-language": lang}).text
    assert new in html and f'<h1 class="page-title">{title}</h1>' in html


@pytest.mark.parametrize(("lang", "word"), [("en", "Application"), ("es", "Aplicación")])
def test_ops_page_per_language(hub: TestClient, lang: str, word: str) -> None:
    assert word in hub.get("/admin/tasks", headers={"accept-language": lang}).text


def test_tr_html_skips_code_attrs_and_notr() -> None:
    src = '<p title="Usuários">Novo usuário <code>Novo usuário</code></p><!--notr-->Entrar<!--/notr-->'
    assert (
        tr_html(src, "en")
        == '<p title="Users">New user <code>Novo usuário</code></p><!--notr-->Entrar<!--/notr-->'
    )
    assert tr_html(src, "pt-BR") == src
