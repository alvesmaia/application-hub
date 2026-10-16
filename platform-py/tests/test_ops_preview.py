"""Task 29: package preview before deploying (`POST /api/ops/packages/preview`) and `config` sent by the UI."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from admin_helper import add_user, make_hub
from fastapi.testclient import TestClient
from test_ops import package

from hub.ops import model as m
from hub.ops import pipeline as pl
from hub.ops.package_preview import current_versions

PREVIEW = "/api/ops/packages/preview"
XH = {"x-hub-request": "1"}
REQ = {"database": True, "variables": ["API_URL"], "secrets": ["API_KEY"]}
SECRET = "s3cr3t-valor-que-nunca-volta"


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with make_hub(tmp_path, monkeypatch) as c:
        yield c


def manifest_pkg(**fields: Any) -> bytes:
    """`package()` of test_ops with manifest fields replaced (checksums recomputed)."""
    man = {"id": "novoapp", "name": "Novo App", "version": "1.2.3", "routes": ["/novoapp/"],
           "permissions": ["novoapp.read"], **fields}  # fmt: skip
    return package({"manifest.json": json.dumps(man).encode()})


def preview(c: TestClient, data: bytes, headers: dict[str, str] | None = None) -> httpx.Response:
    r = c.post(PREVIEW, files={"package": ("p.hubapp", data)}, headers=XH if headers is None else headers)
    return cast(httpx.Response, r)


def test_preview_new_app_lists_requires(hub: TestClient) -> None:
    r = preview(hub, manifest_pkg(requires=REQ))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] and body["error"] is None
    assert (body["id"], body["name"], body["version"], body["new"]) == ("novoapp", "Novo App", "1.2.3", True)
    assert body["current"] == {"producao": "", "staging": ""}
    assert body["requires"] == {
        "database": True,
        "storage": False,
        "variables": ["API_URL"],
        "secrets": ["API_KEY"],
    }
    assert hub.get("/api/ops/deployments").json() == []  # nothing deployed or recorded


def test_preview_update_shows_current_versions(hub: TestClient) -> None:
    store = hub.app.state.ops  # type: ignore[attr-defined]
    d = store.read()
    m.push(d, {"app": "ativos", "version": "1.2.0", "env": m.PRODUCAO, "status": m.IMPLANTADO})
    store.write(d)
    body = preview(hub, manifest_pkg(id="ativos", name="Ativos", version="1.3.0", routes=["/ativos/"])).json()
    assert body["valid"] and not body["new"]
    assert body["current"] == {"producao": "1.2.0", "staging": ""}


def test_current_versions_newest_deployed_and_registry_fallback() -> None:
    deps = [{"app": "a", "env": m.STAGING, "status": m.IMPLANTADO, "version": "2.0"},
            {"app": "a", "env": m.STAGING, "status": m.SUBSTITUIDO, "version": "1.9"},
            {"app": "b", "env": m.PRODUCAO, "status": m.IMPLANTADO, "version": "9"}]  # fmt: skip
    assert current_versions(deps, "a", "1.0") == {"producao": "1.0", "staging": "2.0"}
    assert current_versions(deps, "a", "") == {"producao": "", "staging": "2.0"}


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"nao-e-zip", "Pacote ilegível"),
        (package({"manifest.json": b"{"}), "manifest.json inválido"),
        (manifest_pkg(requires={"variables": ["HUB_X"]}), "requires.variables"),
    ],
)
def test_preview_invalid_manifest_is_reported(hub: TestClient, data: bytes, error: str) -> None:
    r = preview(hub, data)
    assert r.status_code == 200 and not r.json()["valid"] and error in r.json()["error"]


def test_preview_rejects_bad_requests(hub: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert hub.post(PREVIEW, files={"other": ("p", b"x")}, headers=XH).status_code == 400
    assert preview(hub, b"x", headers={}).status_code == 400  # no X-Hub-Request
    assert hub.post(PREVIEW, content=b"x", headers=XH).status_code == 415
    monkeypatch.setattr(pl, "MAX_PACKAGE_BYTES", 10)
    r = preview(hub, manifest_pkg())
    assert r.status_code == 400 and "limite" in r.json()["error"]


def test_preview_requires_permission(hub: TestClient) -> None:
    colab = add_user(hub, "colab", ["Colaborador"])
    assert preview(colab, manifest_pkg()).status_code == 403


def test_preview_token_action_matches_upload() -> None:
    from hub.auth import token_action

    assert token_action("POST", PREVIEW) == "upload"


def test_ui_upload_config_stores_values_without_echoing_secrets(hub: TestClient) -> None:
    req = {"variables": ["API_URL"], "secrets": ["API_KEY"]}
    config = json.dumps({"variables": {"API_URL": "http://api.local"}, "secrets": {"API_KEY": SECRET}})
    r = hub.post("/api/ops/packages?wait=true", files={"package": ("p.hubapp", manifest_pkg(requires=req))},
                 data={"env": "staging", "config": config}, headers=XH)  # fmt: skip
    job = r.json()
    assert job["provisioned"]["secrets"] == ["API_KEY"] and job["provisioned"]["variables"] == ["API_URL"]
    env = hub.app.state.admin.cfg  # type: ignore[attr-defined]
    assert hub.portal is not None
    stored = {e["inject_as"]: e for e in hub.portal.call(env.read)["env"] if e.get("managed")}
    assert stored["API_URL"]["value"] == "http://api.local" and stored["API_KEY"]["kind"] == "secreta"
    seen = [r.text, json.dumps(hub.get(f"/api/ops/jobs/{job['id']}").json()), hub.get("/api/admin/audit?limit=200").text,
            hub.get("/api/admin/logs?minutes=60").text, hub.get("/api/admin/env").text, preview(hub, manifest_pkg(requires=req)).text]  # fmt: skip
    assert all(SECRET not in s for s in seen)


def test_ui_upload_empty_value_keeps_current() -> None:
    from hub.ops import newapp

    cfg = newapp.parse_config(
        json.dumps({"variables": {"API_URL": "", "B": "1"}, "secrets": {"API_KEY": ""}})
    )
    assert cfg == {"variables": {"B": "1"}, "secrets": {}}


def test_import_tab_main_flow_and_validator_in_ci_tab(hub: TestClient) -> None:
    html = hub.get("/admin/deploys").text
    imp = html[html.index('id="p-import"') : html.index('id="p-ci"')]
    ci = html[html.index('id="p-ci"') :]
    assert "data-ops-file" in imp and "data-ops-preview" in imp and "data-ops-env" in imp
    assert "Implantar em staging" in imp and "data-ops-manifest" not in imp
    area = re.search(r"<textarea[^>]*data-ops-manifest[^>]*>(.*?)</textarea>", ci, re.S)
    assert (
        area is not None and area.group(1) == ""
    )  # the example is only a placeholder, never a prefilled value
    assert 'placeholder="{' in area.group(0) and "<details" in ci
    en = hub.get("/admin/deploys", headers={"accept-language": "en"}).text
    assert "Deploy to staging" in en and "Package preview" in en
