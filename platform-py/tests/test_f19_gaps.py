"""F19: production gaps (password rotation, build, pruning, staging, file locking, audit, i18n)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_replace_secret_values_rotates_dsn(hub: TestClient) -> None:
    cfg = hub.app.state.admin.cfg  # type: ignore[attr-defined]
    item = {
        "name": "DB_X",
        "kind": "secreta",
        "value": "postgresql://hub:hubpy-app-dev@db:5432/x",
        "bindings": [],
    }
    asyncio.run(cfg.import_bootstrap("plataforma", [item]))
    new = "N" * 32
    assert asyncio.run(cfg.replace_secret_values("plataforma", {"hubpy-app-dev": new})) == ["DB_X"]
    assert asyncio.run(cfg.replace_secret_values("plataforma", {"hubpy-app-dev": new})) == []
    data = asyncio.run(cfg.read())
    assert next(v for v in data["env"] if v["name"] == "DB_X")["value"] == f"postgresql://hub:{new}@db:5432/x"
    assert (
        new not in str(data["audit"][-1])
        and data["audit"][-1]["title"] == "Senhas de infraestrutura rotacionadas"
    )


def test_build_context_honors_dockerignore() -> None:
    from hub.ops import pipeline as pl

    files = {"build/Dockerfile": b"x", "build/.dockerignore": b"# c\n.venv\n*.log\n", "build/a.py": b"1",
             "build/.venv/x": b"2", "build/out.log": b"3", "manifest.json": b"{}"}  # fmt: skip
    assert sorted(pl.build_context(files)) == [
        "build/.dockerignore",
        "build/Dockerfile",
        "build/a.py",
        "manifest.json",
    ]
    assert pl.build_context({"build/a": b""}) == {"build/a": b""}


class _Img:
    def __init__(self, tags: list[str], when: str) -> None:
        self.tags, self.attrs = tags, {"Metadata": {"LastTagTime": when}}


def test_build_limits_and_prune_pins() -> None:
    from types import SimpleNamespace

    from hub.ops import runtime as rt

    calls: list[dict[str, Any]] = []
    removed: list[str] = []
    imgs = [_Img([f"app-hub-app-a:keep-{i}"], f"2026-10-0{i}") for i in range(1, 6)]
    imgs += [
        _Img(["app-hub-app-a:keep-stg-1"], "2026-10-01"),
        _Img(["app-hub-app-a:baseline-1"], "2026-01-01"),
    ]

    def build(**kw: Any) -> Any:
        calls.append(kw)
        while True:
            yield {"stream": "Step\n"}

    real = SimpleNamespace(
        api=SimpleNamespace(build=build),
        images=SimpleNamespace(list=lambda name: imgs, remove=lambda ref, noprune: removed.append(ref)),
        containers=SimpleNamespace(),
    )
    eng = rt.Engine(lambda: real)
    with pytest.raises(rt.OpsError, match="tempo limite"):
        asyncio.run(eng.build(".", "app-hub-app-a:1", "Dockerfile", 1 << 30, lambda ln: None, 2, 0.05))
    assert calls[0]["container_limits"]["cpusetcpus"] == "0-1"
    gone = asyncio.run(eng.prune_pins("a", 3))
    assert gone == ["app-hub-app-a:keep-2", "app-hub-app-a:keep-1"] == removed


def test_image_repos_follow_container_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """F17: an isolated instance (`HUB_CONTAINER_PREFIX`) never labels or prunes images of production apps."""
    from types import SimpleNamespace

    from hub.ops import pipeline as pl
    from hub.ops import runtime as rt

    monkeypatch.setenv("HUB_CONTAINER_PREFIX", "app-hub-t17-app-")
    assert pl.build_tag("a", "1.0") == "app-hub-t17-app-a:1.0"
    asked: list[str] = []
    real = SimpleNamespace(
        images=SimpleNamespace(list=lambda name: asked.append(name) or [], remove=lambda ref, noprune: None),
        containers=SimpleNamespace(),
    )
    assert asyncio.run(rt.Engine(lambda: real).prune_pins("a", 3)) == []
    assert asked == ["app-hub-t17-app-a"]


def test_staging_bar_only_with_cookie(hub: TestClient) -> None:
    assert "data-hub-staging-bar" not in hub.get("/").text
    cookie = hub.headers["cookie"]
    html = hub.get("/", headers={"cookie": f"{cookie}; hub_env=staging"}).text
    assert (
        "data-hub-staging-bar" in html and "Você está em staging" in html and "/hub-env?to=producao" in html
    )
    en = hub.get("/", headers={"cookie": f"{cookie}; hub_env=staging", "accept-language": "en"}).text
    assert "You are in staging" in en and "Back to production" in en
    css = hub.get("/assets/theme.css").text
    assert ".hub-staging-bar" in css and "html[data-hub-app] .hub-staging-bar{position:fixed" in css


def test_api_errors_translated_and_unknown_app_in_shell(hub: TestClient) -> None:
    r = hub.put("/api/admin/env/NAO_EXISTE", json={"value": "x"}, headers={"accept-language": "es"})
    assert r.status_code >= 400 and "não" not in r.json()["error"], r.text
    page = hub.get("/apps/nao-existe", headers={"accept-language": "en"})
    assert (
        page.status_code == 404
        and 'class="platform-header"' in page.text
        and "Application not found" in page.text
    )


def test_audit_external_head_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hub.admin.chain import AuditChain

    head = tmp_path / "fora" / "head.jsonl"
    monkeypatch.setenv("HUB_AUDIT_HEAD_FILE", str(head))
    ch = AuditChain(tmp_path / "audit")
    ev = {"id": 1, "at": 1, "actor": "a", "title": "t", "detail": "d", "icon": "box", "scope": "platform"}
    ch.append(ev)
    ch.append(ev | {"id": 2})
    assert (ch.external_head() or {}).get("seq") == 2 and ch.verify().ok
    # rollback of the data directory (chain goes back to 1 event): the external anchor flags it
    lines = (tmp_path / "audit" / "chain.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "audit" / "chain.jsonl").write_text(lines[0] + "\n", encoding="utf-8")
    import json

    h = json.loads((tmp_path / "audit" / "head.json").read_text(encoding="utf-8"))
    first = json.loads(lines[0])
    h.update(seq=1, hash=first["hash"], event_id=1)
    (tmp_path / "audit" / "head.json").write_text(json.dumps(h), encoding="utf-8")
    assert "chain older than external head anchor" in AuditChain(tmp_path / "audit").verify().problems


def test_audit_head_webhook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HUB_AUDIT_HEAD_URL: each signed head goes by POST (optional Bearer), in the background; a failure becomes a warning."""
    import json

    import httpx

    from hub.admin import chain as chain_mod
    from hub.vault import Vault

    got: list[tuple[dict[str, object], str]] = []
    status = {"code": 204}

    def handler(req: httpx.Request) -> httpx.Response:
        got.append((json.loads(req.content), req.headers.get("authorization", "")))
        return httpx.Response(status["code"])

    monkeypatch.setattr(chain_mod, "HEAD_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("HUB_AUDIT_HEAD_URL", "https://ancora.example/head")
    monkeypatch.setenv("HUB_AUDIT_HEAD_TOKEN", "tok-123")
    ch = chain_mod.AuditChain(tmp_path / "audit", Vault.in_memory(b"k" * 32))
    ev = {"id": 1, "at": 1, "actor": "a", "title": "t", "detail": "d", "icon": "box", "scope": "platform"}
    ch.append(ev)
    ch.append(ev | {"id": 2})
    assert ch.flush_head(5)
    body, auth = got[-1]
    assert body["seq"] == 2 and body["sig"] and body["pk"] and auth == "Bearer tok-123"
    assert ch.head_error == "" and ch.head_target() == "https://ancora.example/head"
    status["code"] = 500
    ch.append(ev | {"id": 3})
    assert ch.flush_head(5) and "500" in ch.head_error
    ch.close()


def test_product_owner_sees_usage_card_on_audit(hub: TestClient) -> None:
    from admin_helper import add_user

    po = add_user(hub, "po", ["Product Owner"])
    assert po.get("/admin").status_code == 403
    html = po.get("/admin/audit").text
    assert "data-usage-card" in html and "Uso da plataforma (30 dias)" in html
    assert po.get("/admin/analytics", follow_redirects=False).headers["location"] == "/admin/audit#uso"
    assert (
        "data-usage-card" not in hub.get("/admin/audit").text and "data-usage-card" in hub.get("/admin").text
    )


def test_inbox_writes_merge_across_replicas(tmp_path: Path) -> None:
    from hub.ux.inbox import InboxStore

    a, b = InboxStore(tmp_path / "inbox.json"), InboxStore(tmp_path / "inbox.json")
    item = {"kind": "notification", "app": "ativos", "title": "x", "body": "", "source": "", "targets": {}}
    a.push(dict(item))
    b.push(dict(item))  # b rereads under the lock before writing: does not delete a's item
    assert len(InboxStore(tmp_path / "inbox.json").items) == 2
