"""Onda 13 · stack do app no manifesto (``manifest.stack``, opcional e informativo).

O hub mostra estes dados no catálogo de aplicações (cartão do app: passar o mouse, foco do
teclado ou o botão "i" no toque). Mesmas regras do hub (``hub_manifest::validate_stack``):
``language`` obrigatório; ``framework``/``sdk``/``db`` opcionais; até 12 ``libs``; cada
texto com 1–48 caracteres, sem caracteres de controle.

>>> declare("Python 3.12", framework="FastAPI", db="PostgreSQL 16", libs=["segno"])["framework"]
'FastAPI'
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MAX_LIBS = 12
MAX_TEXT = 48


def _ok(v: str) -> bool:
    return 1 <= len(v.strip()) <= MAX_TEXT and not any(ord(c) < 32 or ord(c) == 127 for c in v)


def declare(
    language: str,
    *,
    framework: str | None = None,
    sdk: str | None = None,
    db: str | None = None,
    libs: Sequence[str] = (),
) -> dict[str, Any]:
    """Entrada de ``manifest.stack`` (levanta ``ValueError`` fora das regras do hub)."""
    out: dict[str, Any] = {"language": language}
    for k, v in (("framework", framework), ("sdk", sdk), ("db", db)):
        if v is not None:
            out[k] = v
    for k, v in out.items():
        if not _ok(v):
            raise ValueError(f"stack.{k}: 1 a {MAX_TEXT} caracteres, sem caracteres de controle")
    lib_list = list(libs)
    if len(lib_list) > MAX_LIBS or not all(_ok(x) for x in lib_list):
        raise ValueError(f"stack.libs: até {MAX_LIBS} nomes de 1 a {MAX_TEXT} caracteres")
    if lib_list:
        out["libs"] = lib_list
    return out
