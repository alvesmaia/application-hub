"""Screens, roles, API guards, search and i18n over the real app without a database (the screens
say the database is not configured; admin areas answer 403 to non-admins regardless)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hub_sdk.identity import Identity

from automacoes.i18n import load
from tests.conftest import AS_ADMIN, HUB, Running

SRC = Path(__file__).resolve().parents[1] / "src" / "automacoes"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
USER_PAGES = ("/automacoes", "/automacoes/catalogo", "/automacoes/execucoes")
ADMIN_PAGES = ("/automacoes/pacotes", "/automacoes/processos", "/automacoes/processos/novo",
               "/automacoes/gatilhos", "/automacoes/gatilhos/novo")  # fmt: skip


def assert_clean(path: str, html: str) -> None:
    assert "<script" not in html.lower(), f"{path}: no JavaScript of its own (CONTRATO-APP §13)"
    assert '="/automacoes' not in html, f"{path}: link without the public base"


async def test_user_pages_render_without_db(running: Running) -> None:
    for p in USER_PAGES:
        r = await running.client.get(p)
        assert r.status_code == 200, p
        assert r.headers["cache-control"] == "no-store"
        assert_clean(p, r.text)
        assert "Banco de dados não configurado" in r.text, p
        assert "Conectado como" in r.text and "<strong>Ana</strong>" in r.text


async def test_admin_pages_render_for_admins_and_403_for_others(running: Running) -> None:
    for p in ADMIN_PAGES:
        r = await running.client.get(p, headers=AS_ADMIN)
        assert r.status_code == 200, p
        assert_clean(p, r.text)
        denied = await running.client.get(p)
        assert denied.status_code == 403 and "Acesso restrito" in denied.text, p
    for p in ("/automacoes/pacotes", "/automacoes/processos", "/automacoes/gatilhos", "/automacoes/gatilhos/1/ativo",
              "/automacoes/gatilhos/1/excluir", "/automacoes/processos/1"):  # fmt: skip
        assert (await running.client.post(p, data={})).status_code == 403, p


async def test_sidebar_depends_on_the_role(running: Running) -> None:
    user = (await running.client.get("/automacoes")).text
    admin = (await running.client.get("/automacoes", headers=AS_ADMIN)).text
    for s in ("catalogo", "execucoes"):
        assert f'href="/apps/automacoes/{s}"' in user and f'href="/apps/automacoes/{s}"' in admin
    for s in ("processos", "pacotes", "gatilhos"):
        assert f'href="/apps/automacoes/{s}"' not in user and f'href="/apps/automacoes/{s}"' in admin


async def test_admin_roles_come_from_the_environment(
    running: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOMACOES_ADMIN_ROLES", "Operador, Auditor")
    assert (await running.client.get("/automacoes/pacotes", headers=AS_ADMIN)).status_code == 403
    operator = Identity("op@demo.local", roles=("Operador",)).encode()
    assert (
        await running.client.get("/automacoes/pacotes", headers={"x-hub-user": operator})
    ).status_code == 200


async def test_upload_form_is_multipart_with_progress(running: Running) -> None:
    page = (await running.client.get("/automacoes/pacotes", headers=AS_ADMIN)).text
    assert (
        'enctype="multipart/form-data"' in page and "data-hub-progress" in page and 'name="arquivo"' in page
    )


async def test_upload_without_file_is_422(running: Running) -> None:
    r = await running.client.post("/automacoes/pacotes", data={}, headers=AS_ADMIN)
    assert r.status_code == 422 and "Escolha um arquivo .zip." in r.text


async def test_links_follow_the_staging_base(running: Running) -> None:
    r = await running.client.get("/automacoes", headers={"x-hub-base": "/staging/apps/automacoes"})
    assert 'href="/staging/apps/automacoes/execucoes"' in r.text


async def test_unknown_section_skeleton_and_assets(running: Running) -> None:
    c = running.client
    assert (await c.get("/automacoes/nao-existe")).status_code == 404
    assert (await c.get("/automacoes/_skeleton")).status_code == 200
    css = await c.get("/automacoes/assets/app.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert "--app-chrome:" in css.text and ':root[data-theme="dark"]' in css.text
    assert (await c.get("/automacoes/assets/demo.css")).status_code == 200
    assert (await c.get("/automacoes/assets/hub.js")).status_code == 404


async def test_runs_without_db(running: Running) -> None:
    c = running.client
    assert (await c.post("/automacoes/catalogo/1/executar", data={})).status_code == 503
    assert (await c.get("/automacoes/execucoes/1")).status_code == 503
    assert (await c.get("/automacoes/execucoes/1/estado")).status_code == 404
    assert (await c.post("/automacoes/execucoes/1/reexecutar")).status_code == 404
    assert (await c.post("/automacoes/execucoes/x/cancelar")).status_code == 404


async def test_api_guards(running: Running) -> None:
    c = running.client
    async with running.anon() as anon:
        assert (await anon.get("/automacoes/api/v1/processos")).status_code == 401
    for method, path in (("GET", "/pacotes"), ("POST", "/pacotes/base64"), ("POST", "/processos"),
                         ("PUT", "/processos/1")):  # fmt: skip
        body = {"content_base64": "", "name": "x", "package_name": "p", "package_version": "1.0.0"}
        r = await c.request(method, f"/automacoes/api/v1{path}", json=body)
        assert r.status_code == 403, (method, path, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")
    r = await c.post("/automacoes/api/v1/pacotes", files={"arquivo": ("p.zip", b"x")})
    assert r.status_code == 403
    assert (await c.get("/automacoes/api/v1/processos")).status_code == 503
    assert (await c.post("/automacoes/api/v1/processos/1/execucoes", json={"params": {}})).status_code == 503
    assert (await c.get("/automacoes/api/v1/execucoes")).status_code == 503
    assert (await c.get("/automacoes/api/v1/execucoes/1")).status_code == 503
    r = await c.post("/automacoes/api/v1/processos/1/execucoes", json={"params": "x"})
    assert r.status_code == 422 and r.json()["status"] == 422


def test_packaged_openapi_matches_the_code(env: Path) -> None:
    from automacoes.app import create_app

    doc = create_app().state.hub_api_doc()
    assert json.loads(OPENAPI.read_text("utf-8")) == doc
    ops = {o["operationId"] for item in doc["paths"].values() for o in item.values()}
    assert ops == {"list_packages", "upload_package", "upload_package_base64", "list_processes", "create_process",
                   "update_process", "start_run", "get_run", "list_runs"}  # fmt: skip


async def test_readiness_health_and_search(running: Running) -> None:
    c = running.client
    assert (await c.get("/automacoes/_hub/ready")).status_code == 200
    assert (await c.get("/automacoes/_hub/health")).json() == {"status": "ok"}
    r = await c.get("/automacoes/_hub/search?q=cep&limit=5", headers=HUB)
    assert r.status_code == 200 and r.json()["results"] == [], "no database: nothing to find"
    assert (await c.get("/automacoes/_hub/search?q=x")).status_code in (401, 403)


async def test_pages_in_english(running: Running) -> None:
    page = (
        await running.client.get("/automacoes/execucoes", headers={"x-hub-locale": "en", **AS_ADMIN})
    ).text
    for text in ("Runs", "Processes", "Packages", "Triggers", "Last 30 days", "Database not configured"):
        assert text in page, text


TEXT_CALL = re.compile(r"""\bt\(\s*(['"])(.+?)(?<!\\)\1""")


def literal_texts() -> set[str]:
    """Every ``t('…')`` literal in the templates and the Python code, the status/origin labels,
    the sidebar and the sample packages' texts."""
    from automacoes.app import ADMIN_NAV
    from automacoes.model import STATUS_LABELS, TRIGGER_LABELS
    from tests.samples import SAMPLES, manifest

    found: set[str] = set()
    for p in [*SRC.rglob("*.html"), *SRC.rglob("*.py")]:
        found |= {m[1] for m in TEXT_CALL.findall(p.read_text("utf-8"))}
    found |= set(STATUS_LABELS.values()) | set(TRIGGER_LABELS.values()) | {n.label for n in ADMIN_NAV}
    found |= {"Automações", "Orquestrador de automações"}
    for folder in (p for p in SAMPLES.iterdir() if p.is_dir()):
        m = manifest(folder.name)
        found |= {m.description, *m.labels.values()}
        found |= {x for f in m.fields for x in (f.label, f.help, *(lab for _, lab in f.options)) if x}
    return found


def test_every_text_has_an_english_translation() -> None:
    catalog = load()
    missing = sorted(t for t in literal_texts() if t not in catalog)
    assert missing == [], missing
    assert all(v["en"].strip() for v in catalog.values())


def test_modal_form_posts_through_the_hub_kit() -> None:
    """The run form lives in a modal (outside #app-root): a plain method=post form is submitted by the browser
    straight to /apps/automacoes/… and the gateway answers 405. Modal forms use data-hub-post (CONTRATO-APP)."""
    src = (SRC / "templates" / "_run_form.html").read_text("utf-8")
    form = re.search(r"<form[^>]*>", src)
    assert form
    assert 'data-hub-post="{{ base }}/catalogo/' in form.group(0)
    assert 'method="post"' not in form.group(0)
