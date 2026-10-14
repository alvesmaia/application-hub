"""Suíte de conformidade do contrato de app (``docs/CONTRATO-APP.md``) — caixa-preta por
HTTP, independente de linguagem.

Configuração (variáveis de ambiente):

- ``CONF_APP`` — id do app; o perfil ``profiles/<id>.json`` diz as seções, a ação que
  grava dados (idempotência + auditoria), o gatilho de tarefa e a rota pesada;
- ``CONF_URL`` — base do app (ex.: ``http://hub-app-contratos:8080`` na rede do app, ou
  ``http://127.0.0.1:9104`` nativo);
- ``CONF_TOKEN`` — ``HUB_INTERNAL_TOKEN`` do app;
- ``CONF_DB_URL`` — opcional: banco do app (verificações de esquema e de efeitos);
- ``CONF_PROFILE`` — opcional: caminho do perfil (o ``hub check --full`` usa o ``conformance.json`` do app).

O hub **não** deve estar coletando o outbox do app durante a suíte (o ``run.sh`` para o
``hub-web``): a suíte confere os eventos pendentes e as confirmações ela mesma.
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
