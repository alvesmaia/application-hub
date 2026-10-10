"""Onda 12 (2.1) · JavaScript próprio do app — **exceção** declarada no manifesto.

Por padrão um app não roda JavaScript: os comportamentos vêm dos atributos ``data-hub-*``
do hub (docs/CONTRATO-APP.md §25). Quando não há alternativa, o app declara o arquivo em
``manifest.scripts`` com o motivo; no import o hub calcula o sha384, o pipeline mostra
"REVISE: App traz JavaScript próprio" e só a aprovação explícita da administração o libera
(CSP com o hash + carga com SRI; hash novo = nova aprovação).

O arquivo fica no pacote em ``ui/assets/<nome>.js`` e o app o serve em
``/{id}/assets/<nome>.js`` com **os mesmos bytes** (senão o navegador recusa pelo SRI).
Depois de cada troca de fragmento o hub dispara ``hub:mount`` no ``document``.

>>> declare("editor.js", "Editor de texto rico sem equivalente declarativo")["path"]
'ui/assets/editor.js'
>>> integrity(b"abc")
'sha384-ywB1P0WjXou1oD1pmsZQBycsMqsO3tFjGotgWkP/W+2AhgcroefMI1i67KE0yCWn'
"""

from __future__ import annotations

import base64
import hashlib
import re

_NAME = re.compile(r"^[a-z0-9._-]{1,61}\.js$")


def declare(file: str, reason: str) -> dict[str, str]:
    """Entrada de ``manifest.scripts`` para ``ui/assets/<file>`` (mesmas regras do hub)."""
    if not _NAME.match(file) or file.startswith(".") or ".." in file or len(file) < 4:
        raise ValueError(f"nome de script inválido: {file!r} (use <nome>.js, minúsculas)")
    text = reason.strip()
    if not 10 <= len(text) <= 500 or any(ord(c) < 32 or ord(c) == 127 for c in reason):
        raise ValueError("motivo com 10–500 caracteres é obrigatório")
    return {"path": f"ui/assets/{file}", "reason": text}


def integrity(data: bytes) -> str:
    """Integridade SRI/CSP (``sha384-<base64>``) — a mesma que o hub calcula no import."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
