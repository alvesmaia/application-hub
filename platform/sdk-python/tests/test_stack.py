"""Onda 13: ``hub_sdk.stack.declare`` com as mesmas regras do hub (manifest.stack)."""

from __future__ import annotations

from typing import Any

import pytest

from hub_sdk.stack import declare


def test_declare_minimal_and_full() -> None:
    assert declare("Go 1.23") == {"language": "Go 1.23"}
    d = declare(
        "Python 3.12", framework="FastAPI", sdk="hub-sdk-python 1.0", db="PostgreSQL 16", libs=["segno"]
    )
    assert d == {
        "language": "Python 3.12",
        "framework": "FastAPI",
        "sdk": "hub-sdk-python 1.0",
        "db": "PostgreSQL 16",
        "libs": ["segno"],
    }


@pytest.mark.parametrize(
    "kw",
    [
        {"language": ""},
        {"language": "x" * 49},
        {"language": "Go", "framework": "a\x07"},
        {"language": "Go", "libs": [f"l{i}" for i in range(13)]},
    ],
)
def test_declare_rejects(kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        declare(**kw)
