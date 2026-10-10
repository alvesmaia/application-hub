"""As rotas de teste anti-phishing só existem com HUB_DEMO_E2E_HOSTILE=1."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hub_sdk import e2e_hostile


def test_routes_off_by_default_and_on_with_env(monkeypatch):
    monkeypatch.delenv(e2e_hostile.ENV, raising=False)
    off = FastAPI()
    e2e_hostile.install(off, "ativos")
    assert TestClient(off).get("/ativos/e2e-hostil").status_code == 404

    monkeypatch.setenv(e2e_hostile.ENV, "1")
    on = FastAPI()
    e2e_hostile.install(on, "ativos")
    c = TestClient(on)
    page = c.get("/ativos/e2e-hostil")
    assert page.status_code == 200 and "data-e2e-ok" in page.text and "/apps/ativos/e2e-hostil" in page.text
    assert "data-e2e-posted-own" in c.post("/ativos/e2e-hostil").text
    assert c.get("/ativos/assets/e2e-approved.js").text == e2e_hostile.APPROVED_JS
    assert c.get("/ativos/assets/e2e-tampered.js").text == e2e_hostile.TAMPERED_JS
