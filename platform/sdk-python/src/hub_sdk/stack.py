"""Wave 13 · app stack in the manifest (``manifest.stack``, optional and informational).

The hub shows this data in the applications catalog (app card: hover, focus of the
keyboard or the "i" button on touch). Same rules as the hub (``hub_manifest::validate_stack``):
``language`` required; ``framework``/``sdk``/``db`` optional; up to 12 ``libs``; each
text with 1–48 characters, no control characters.

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
    """Entry of ``manifest.stack`` (raises ``ValueError`` outside the hub's rules)."""
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
