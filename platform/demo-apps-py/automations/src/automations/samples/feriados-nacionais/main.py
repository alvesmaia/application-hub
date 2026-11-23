"""Sample package: national holidays of a year (BrasilAPI). Base URL: ``AUTOMATIONS_BRASILAPI_URL``."""

from __future__ import annotations

import os
from typing import Any

import httpx


def run(params: dict[str, Any], transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    year = int(params["ano"])
    base = os.environ.get("AUTOMATIONS_BRASILAPI_URL", "").strip() or "https://brasilapi.com.br"
    with httpx.Client(timeout=10, transport=transport) as http:
        r = http.get(f"{base.rstrip('/')}/api/feriados/v1/{year}")
    if r.status_code == 404:
        raise LookupError("Ano sem feriados disponíveis")
    if r.status_code >= 400 or not isinstance(body := r.json(), list):
        raise RuntimeError(f"BrasilAPI respondeu HTTP {r.status_code}")
    holidays = [{"data": str(h.get("date", "")), "nome": str(h.get("name", ""))} for h in body]
    return {"ano": year, "quantidade": len(holidays), "feriados": holidays}
