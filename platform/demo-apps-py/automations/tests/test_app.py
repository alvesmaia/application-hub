"""Screens, roles, API guards and i18n over the real app without a database (the screens
say the database is not configured; admin areas answer 403 to non-admins regardless)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hub_sdk.identity import Identity

from automations.i18n import load
from tests.conftest import AS_ADMIN, Running

SRC = Path(__file__).resolve().parents[1] / "src" / "automations"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
USER_PAGES = ("/automations", "/automations/catalog", "/automations/runs")
ADMIN_PAGES = ("/automations/packages", "/automations/processes", "/automations/processes/new",
               "/automations/triggers", "/automations/triggers/new")  # fmt: skip


def assert_clean(path: str, html: str) -> None:
    assert "<script" not in html.lower(), f"{path}: no JavaScript of its own (CONTRATO-APP §13)"
    assert '="/automations' not in html, f"{path}: link without the public base"


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
    for p in ("/automations/packages", "/automations/processes", "/automations/triggers", "/automations/triggers/1/enabled",
              "/automations/triggers/1/delete", "/automations/processes/1"):  # fmt: skip
        assert (await running.client.post(p, data={})).status_code == 403, p


async def test_sidebar_depends_on_the_role(running: Running) -> None:
    user = (await running.client.get("/automations")).text
    admin = (await running.client.get("/automations", headers=AS_ADMIN)).text
    for s in ("catalog", "runs"):
        assert f'href="/apps/automations/{s}"' in user and f'href="/apps/automations/{s}"' in admin
    for s in ("processes", "packages", "triggers"):
        assert f'href="/apps/automations/{s}"' not in user and f'href="/apps/automations/{s}"' in admin


async def test_admin_roles_come_from_the_environment(
    running: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOMATIONS_ADMIN_ROLES", "Operador, Auditor")
    assert (await running.client.get("/automations/packages", headers=AS_ADMIN)).status_code == 403
    operator = Identity("op@demo.local", roles=("Operador",)).encode()
    assert (
        await running.client.get("/automations/packages", headers={"x-hub-user": operator})
    ).status_code == 200


async def test_upload_form_is_multipart_with_progress(running: Running) -> None:
    page = (await running.client.get("/automations/packages", headers=AS_ADMIN)).text
    assert (
        'enctype="multipart/form-data"' in page and "data-hub-progress" in page and 'name="arquivo"' in page
    )


async def test_upload_without_file_is_422(running: Running) -> None:
    r = await running.client.post("/automations/packages", data={}, headers=AS_ADMIN)
    assert r.status_code == 422 and "Escolha um arquivo .zip." in r.text


async def test_links_follow_the_staging_base(running: Running) -> None:
    r = await running.client.get("/automations", headers={"x-hub-base": "/staging/apps/automations"})
    assert 'href="/staging/apps/automations/runs"' in r.text


async def test_unknown_section_skeleton_and_assets(running: Running) -> None:
    c = running.client
    assert (await c.get("/automations/nao-existe")).status_code == 404
    assert (await c.get("/automations/_skeleton")).status_code == 200
    css = await c.get("/automations/assets/app.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert "--app-chrome:" in css.text and ':root[data-theme="dark"]' in css.text
    assert (await c.get("/automations/assets/demo.css")).status_code == 200
    assert (await c.get("/automations/assets/hub.js")).status_code == 404


async def test_runs_without_db(running: Running) -> None:
    c = running.client
    assert (await c.post("/automations/catalog/1/run", data={})).status_code == 503
    assert (await c.get("/automations/runs/1")).status_code == 503
    assert (await c.get("/automations/runs/1/status")).status_code == 404
    assert (await c.post("/automations/runs/1/rerun")).status_code == 404
    assert (await c.post("/automations/runs/x/cancel")).status_code == 404


async def test_api_guards(running: Running) -> None:
    c = running.client
    async with running.anon() as anon:
        assert (await anon.get("/automations/api/v1/processes")).status_code == 401
    for method, path in (("GET", "/packages"), ("POST", "/packages/base64"), ("POST", "/processes"),
                         ("PUT", "/processes/1")):  # fmt: skip
        body = {"content_base64": "", "name": "x", "package_name": "p", "package_version": "1.0.0"}
        r = await c.request(method, f"/automations/api/v1{path}", json=body)
        assert r.status_code == 403, (method, path, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")
    r = await c.post("/automations/api/v1/packages", files={"arquivo": ("p.zip", b"x")})
    assert r.status_code == 403
    assert (await c.get("/automations/api/v1/processes")).status_code == 503
    assert (await c.post("/automations/api/v1/processes/1/runs", json={"params": {}})).status_code == 503
    assert (await c.get("/automations/api/v1/runs")).status_code == 503
    assert (await c.get("/automations/api/v1/runs/1")).status_code == 503
    r = await c.post("/automations/api/v1/processes/1/runs", json={"params": "x"})
    assert r.status_code == 422 and r.json()["status"] == 422


def test_packaged_openapi_matches_the_code(env: Path) -> None:
    from automations.app import create_app

    doc = create_app().state.hub_api_doc()
    assert json.loads(OPENAPI.read_text("utf-8")) == doc
    ops = {o["operationId"] for item in doc["paths"].values() for o in item.values()}
    assert ops == {"list_packages", "upload_package", "upload_package_base64", "list_processes", "create_process",
                   "update_process", "start_run", "get_run", "list_runs"}  # fmt: skip


async def test_readiness_and_health(running: Running) -> None:
    c = running.client
    assert (await c.get("/automations/_hub/ready")).status_code == 200
    assert (await c.get("/automations/_hub/health")).json() == {"status": "ok"}


async def test_pages_in_english(running: Running) -> None:
    page = (await running.client.get("/automations/runs", headers={"x-hub-locale": "en", **AS_ADMIN})).text
    for text in ("Runs", "Processes", "Packages", "Triggers", "Last 30 days", "Database not configured"):
        assert text in page, text


TEXT_CALL = re.compile(r"""\bt\(\s*(['"])(.+?)(?<!\\)\1""")


def literal_texts() -> set[str]:
    """Every ``t('…')`` literal in the templates and the Python code, the status/origin labels,
    the sidebar and the sample packages' texts."""
    from automations.app import ADMIN_NAV
    from automations.model import STATUS_LABELS, TRIGGER_LABELS
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
    straight to /apps/automations/… and the gateway answers 405. Modal forms use data-hub-post (CONTRATO-APP)."""
    src = (SRC / "templates" / "_run_form.html").read_text("utf-8")
    form = re.search(r"<form[^>]*>", src)
    assert form
    assert 'data-hub-post="{{ base }}/catalog/' in form.group(0)
    assert 'method="post"' not in form.group(0)
