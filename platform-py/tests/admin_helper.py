"""Test hub for Administration: data in a temporary disk, five apps in the registry and per-role sessions."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from argon2 import PasswordHasher
from auth_helper import client_for
from fastapi.testclient import TestClient

from hub.app import create_app
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import AppEntry, Registry

HDR = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
APPS = ("ativos", "coletor", "fornecedores", "servicos")
_PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
PASSWORD = "senha-forte-123"


def registry() -> Registry:
    return Registry(
        [
            AppEntry(
                id=a,
                name=f"App {a.title()}",
                upstream="http://upstream",
                display={
                    "short": a.title(),
                    "color": "#e0f2f1",
                    "ink": "#0f766e",
                    "icon": "box",
                    "description": a,
                },
            )
            for a in APPS
        ]
    )


def make_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, handler: Any = None) -> TestClient:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_STORAGE_ALLOW_HOSTS", "objstore")
    for k in (
        "HUB_APP_ENV_FILE",
        "HUB_BROKER_ADDR",
        "HUB_S3_ADMIN_URL",
        "HUB_GRAPH_URL",
        "HUB_GRAPH_LOGIN_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(404)))
    client = client_for(
        create_app(gateway=Gateway(registry(), GwOptions(user_sig_key=b"k" * 32), transport=transport))
    )
    client.headers.update(HDR)
    return client


def add_user(client: TestClient, user_id: str, roles: list[str], *, active: bool = True) -> TestClient:
    """Creates a user with its own session and returns an HTTP client authenticated as that user."""
    auth = client.app.state.auth  # type: ignore[attr-defined]
    doc = json.loads(auth.path.read_text(encoding="utf-8"))
    now = time.time_ns() // 1_000_000
    doc["users"].append(
        {
            "id": user_id,
            "email": f"{user_id}@demo.local",
            "name": user_id.title(),
            "roles": roles,
            "active": active,
            "password_hash": _PH.hash(PASSWORD),
            "must_change_password": False,
            "created_at": now,
            "updated_at": now,
            "last_login": None,
            "failed_attempts": 0,
            "locked_until": None,
        }
    )
    cookie = f"cookie-{user_id}"
    doc["sessions"].append(
        {
            "id_hash": hashlib.sha256(cookie.encode()).hexdigest(),
            "user_id": user_id,
            "created_at": now,
            "last_seen": now,
            "method": "password",
        }
    )
    auth.path.write_text(json.dumps(doc), encoding="utf-8")
    other = TestClient(client.app, follow_redirects=False)
    other.headers.update(HDR)
    other.headers["cookie"] = f"hub_session={cookie}"
    return other
