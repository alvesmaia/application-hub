"""Sample package (offline): CPF/CNPJ check digits of a list, one document per line."""

from __future__ import annotations

from typing import Any

from documentos import validate_lines


def run(params: dict[str, Any]) -> dict[str, Any]:
    return validate_lines(str(params["documentos"]), str(params.get("tipo") or "auto"))
