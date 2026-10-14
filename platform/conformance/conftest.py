"""Conformance suite for the app contract (``docs/CONTRATO-APP.md``) — black box over
HTTP, language-independent.

Configuration (environment variables):

- ``CONF_APP`` — app id; the profile ``profiles/<id>.json`` lists the sections, the action that
  writes data (idempotency + audit), the task trigger and the heavy route;
- ``CONF_URL`` — app base URL (e.g. ``http://hub-app-contratos:8080`` on the app network, or
  ``http://127.0.0.1:9104`` native);
- ``CONF_TOKEN`` — the app's ``HUB_INTERNAL_TOKEN``;
- ``CONF_DB_URL`` — optional: the app database (schema and side-effect checks);
- ``CONF_PROFILE`` — optional: profile path (``hub check --full`` uses the app's ``conformance.json``).

The hub **must not** be collecting the app's outbox during the suite (``run.sh`` stops the
``hub-web``): the suite checks the pending events and the acknowledgements itself.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

HERE = Path(__file__).resolve().parent


def ident(user: str, name: str = "", roles: list[str] | None = None) -> str:
    d: dict[str, Any] = {"user": user, "roles": roles or []}
    if name:
        d["name"] = name
    return base64.urlsafe_b64encode(json.dumps(d, ensure_ascii=False).encode()).rstrip(b"=").decode()


class Cfg:
    def __init__(self) -> None:
        self.app = os.environ.get("CONF_APP", "").strip()
        if not self.app:
            pytest.exit("defina CONF_APP (e CONF_URL, CONF_TOKEN)", returncode=2)
        self.url = os.environ.get("CONF_URL", "").rstrip("/")
        self.token = os.environ.get("CONF_TOKEN", "")
        self.db_url = os.environ.get("CONF_DB_URL", "")
        prof = Path(os.environ.get("CONF_PROFILE") or HERE / "profiles" / f"{self.app}.json")
        self.profile: dict[str, Any] = json.loads(prof.read_text("utf-8"))
        self.uniq = secrets.token_hex(4).upper()
        self.user = f"conformidade-{self.uniq.lower()}@teste.local"
        self.name = "Conformidade <Zé>"
        self.header = ident(self.user, self.name, ["Leitor"])

    def fill(self, v: Any, uniq: str | None = None) -> Any:
        u = uniq or self.uniq
        if isinstance(v, str):
            return v.replace("{uniq}", u)
        if isinstance(v, dict):
            return {k: self.fill(x, u) for k, x in v.items()}
        return v


@pytest.fixture(scope="session")
def cfg() -> Cfg:
    return Cfg()


@pytest.fixture(scope="session")
def http(cfg: Cfg) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=cfg.url, headers={"x-hub-user": cfg.header}, timeout=60) as c:
        yield c


@pytest.fixture(scope="session")
def internal(cfg: Cfg) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=cfg.url, headers={"x-hub-internal-token": cfg.token}, timeout=60) as c:
        yield c
