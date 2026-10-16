"""Containers page: the app's database/token actions live in its details (they left /admin/env)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient
from test_ops import FakeDocker, upstream

from hub.ux.texts import tr

JSON = {"content-type": "application/json", "x-hub-request": "1"}


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch, upstream) as c:
        c.app.state.ops_engine.factory = FakeDocker  # type: ignore[attr-defined]
        yield c


def detail(page: str, key: str) -> str:
    return page.split(f'data-ops-detail="{key}"', 1)[1].split('class="ops-detail"', 1)[0]


def test_app_details_have_db_and_token_actions(hub: TestClient) -> None:
    ativos = detail(hub.get("/admin/runtime").text, "ativos|producao")
    assert 'data-ops-provision="ativos"' in ativos and "Provisionar banco" in ativos
    assert 'data-ops-provision="ativos" data-env="staging"' in ativos
    assert 'data-ops-rotate="ativos|token"' in ativos
    assert 'data-ops-rotate="ativos|database"' not in ativos  # no database yet
    assert (
        'data-requires="editar_configuracoes"' in ativos and 'data-requires="gerenciar_variaveis"' in ativos
    )


def test_app_with_database_can_check_and_rotate_its_password(hub: TestClient) -> None:
    var = {"name": "DB_ATIVOS", "kind": "connection_string", "inject_as": "HUB_DB_URL", "value": "postgres://u:p@h/db",
           "bindings": [{"app": "ativos", "env": "producao"}]}  # fmt: skip
    assert hub.post("/api/admin/env", json=var).status_code == 201
    ativos = detail(hub.get("/admin/runtime").text, "ativos|producao")
    assert "Verificar banco" in ativos and 'data-ops-rotate="ativos|database"' in ativos


def test_platform_containers_have_no_db_actions(hub: TestClient) -> None:
    page = hub.get("/admin/runtime").text
    plat = detail(page, "app-hub-app-db|platform")
    assert "data-ops-provision" not in plat and "data-ops-rotate" not in plat


def test_messages_point_to_the_app_details() -> None:
    where = "Containers › app details"
    for pt in (
        "banco não provisionado pela plataforma: use Provisionar banco em Containers › detalhes do app",
        "o papel app_x não existe (ou não é do hub): use Provisionar banco em Containers › detalhes do app",
        "Staging sem banco próprio: provisione o banco de staging (Containers › detalhes do app › Banco próprio de staging) antes de clonar.",
        "Staging sem banco próprio: provisione o banco de staging (Containers › detalhes do app › Banco próprio de staging) antes de implantar em staging.",
        "Contrato de variáveis de staging: DB vale em produção e em staging. Staging nunca recebe credenciais de banco de produção: tire o vínculo de staging e provisione o banco de staging (Containers › detalhes do app › Banco próprio de staging).",
    ):
        assert where in tr(pt, "en"), pt


def test_rotate_error_names_the_new_place(hub: TestClient) -> None:
    r = hub.post("/api/ops/rotate/coletor/database", json={}, headers={"accept-language": "en"})
    assert r.status_code == 409 and "Containers › app details" in r.text
