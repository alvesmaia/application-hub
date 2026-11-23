"""Sample package: the address of a CEP (ViaCEP). Base URL: ``AUTOMACOES_VIACEP_URL``."""

from __future__ import annotations

import os
from typing import Any

import httpx

KEYS = ("cep", "logradouro", "complemento", "bairro", "localidade", "uf", "estado", "ibge", "ddd")


def run(params: dict[str, Any], transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    cep = str(params["cep"]).replace("-", "")
    base = os.environ.get("AUTOMACOES_VIACEP_URL", "").strip() or "https://viacep.com.br"
    with httpx.Client(timeout=10, transport=transport) as http:
        r = http.get(f"{base.rstrip('/')}/ws/{cep}/json/")
    if r.status_code >= 400:
        raise RuntimeError(f"ViaCEP respondeu HTTP {r.status_code}")
    body = r.json()
    # ViaCEP answers 200 with {"erro": true} (or "true") for a well-formed CEP that does not exist.
    if not isinstance(body, dict) or str(body.get("erro", "")).lower() == "true":
        raise LookupError("CEP não encontrado")
    return {k: body[k] for k in KEYS if body.get(k) not in (None, "")}
