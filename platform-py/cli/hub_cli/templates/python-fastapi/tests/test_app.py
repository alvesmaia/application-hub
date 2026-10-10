"""Testes do app sobre o app real (lifespan incluído), banco SQLite descartável."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from __PKG__.__main__ import main

IDENT = json.dumps({"user": "ana@exemplo", "name": "Ana"}).encode()
USER = base64.urlsafe_b64encode(IDENT).decode().rstrip("=")
H = {"x-hub-user": USER, "x-hub-base": "/apps/__APP_ID__"}
A = "/__APP_ID__"  # caminho do app no upstream


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("HUB_SCRATCH_DIR", str(tmp_path))
    monkeypatch.delenv("HUB_DB_URL", raising=False)
    from __PKG__.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_pages_use_public_base(client: TestClient) -> None:
    r = client.get(f"{A}", headers=H)
    assert r.status_code == 200 and 'class="workspace' in r.text
    assert 'href="/apps/__APP_ID__/itens"' in r.text
    assert 'href="/__APP_ID__/' not in r.text
    assert client.get(f"{A}/itens", headers=H).status_code == 200
    assert client.get(f"{A}/nada", headers=H).status_code == 404
    assert client.get(f"{A}/_skeleton").status_code == 200
    assert client.get(f"{A}/assets/app.css").headers["content-type"].startswith("text/css")
    assert client.get(f"{A}/assets/x.js").status_code == 404


def test_form_creates_item_and_validates(client: TestClient) -> None:
    r = client.post(f"{A}/itens", data={"name": "Parafuso", "quantity": "7"}, headers=H)
    assert r.status_code == 200 and "Parafuso" in r.text
    bad = client.post(f"{A}/itens", data={"name": "", "quantity": "x"}, headers=H)
    assert bad.status_code == 422
    assert "7" in client.get(f"{A}", headers=H).text


def test_api(client: TestClient) -> None:
    assert client.get(f"{A}/api/v1/itens").status_code == 401
    assert client.post(f"{A}/api/v1/itens", json={"name": "x", "quantity": 1}).status_code == 401
    r = client.post(f"{A}/api/v1/itens", json={"name": "Porca", "quantity": 3}, headers=H)
    assert r.status_code == 201 and r.json()["name"] == "Porca"
    assert [i["name"] for i in client.get(f"{A}/api/v1/itens", headers=H).json()] == ["Porca"]
    assert client.post(f"{A}/api/v1/itens", json={"name": ""}, headers=H).status_code == 422


def test_ready_and_openapi_in_sync(client: TestClient) -> None:
    assert client.get(f"{A}/_hub/ready").status_code == 200
    doc = client.get(f"{A}/api/openapi.json").json()
    assert {"listar_itens", "criar_item"} <= {
        op["operationId"] for item in doc["paths"].values() for op in item.values()
    }
    committed = Path(__file__).resolve().parents[1] / "openapi.json"
    if committed.is_file():  # `hub check` gera; o pacote leva o mesmo documento
        stale = json.loads(committed.read_text(encoding="utf-8")) != doc
        assert not stale, "openapi.json desatualizado: rode `hub check`, que gera o arquivo de novo"


def test_main_health_without_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "1")
    assert main(["--health"]) == 1
