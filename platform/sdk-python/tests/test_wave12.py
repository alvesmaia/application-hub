"""Wave 12 (2.1): declaration of the app's own JavaScript (exception reviewed by the hub)."""

import pytest

from hub_sdk import scripts


def test_declare_follows_hub_rules() -> None:
    assert scripts.declare("editor.js", "  motivo suficiente  ") == {
        "path": "ui/assets/editor.js",
        "reason": "motivo suficiente",
    }
    for bad in ("Editor.js", "../x.js", "editor.css", ".x.js"):
        with pytest.raises(ValueError):
            scripts.declare(bad, "motivo suficiente")
    with pytest.raises(ValueError):
        scripts.declare("editor.js", "curto")


def test_integrity_matches_browsers() -> None:
    assert scripts.integrity(b"abc") == (
        "sha384-ywB1P0WjXou1oD1pmsZQBycsMqsO3tFjGotgWkP/W+2AhgcroefMI1i67KE0yCWn"
    )


# ---- 2.5: mandatory readiness (GET /{app}/_hub/ready) -------------------------------


def test_ready_endpoint_flips_and_health_is_liveness() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hub_sdk import ready

    state = {"migrated": False}
    rd = ready.Readiness().flag("db", "migrações em curso", lambda: state["migrated"])
    app = FastAPI()
    app.include_router(ready.router("demo", rd))
    c = TestClient(app)
    r = c.get("/demo/_hub/ready")
    assert r.status_code == 503
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "status": "not_ready",
        "reason": "db: migrações em curso",
        "checks": {"db": "migrações em curso"},
    }
    assert c.get("/demo/_hub/health").json() == {"status": "ok"}
    state["migrated"] = True
    r = c.get("/demo/_hub/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"db": "ok"}}


def test_sdk_readiness_includes_shutdown_and_extra_checks() -> None:
    import asyncio

    from hub_sdk import ready
    from hub_sdk.app import HubSdk

    sdk = HubSdk("demo", None)

    async def broker() -> None:
        raise ready.NotReady("broker fora")

    sdk.ready_checks.append(("broker", broker))
    ok, body = asyncio.run(sdk.readiness().evaluate())
    assert not ok and body["reason"] == "broker: broker fora"
    assert body["checks"]["shutdown"] == "ok"
    sdk.ready_checks.clear()
    sdk.shutdown.trigger()
    ok, body = asyncio.run(sdk.readiness().evaluate())
    assert not ok and body["reason"].startswith("shutdown:")
