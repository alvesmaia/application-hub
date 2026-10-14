"""Wave 12 (2.1) · the app's own JavaScript — a **declared exception** in the manifest.

By default an app runs no JavaScript: behaviors come from the hub's ``data-hub-*``
attributes (docs/CONTRATO-APP.md §25). When there is no alternative, the app declares the file in
``manifest.scripts`` with the reason; on import the hub computes the sha384, the pipeline shows
"REVISE: App ships its own JavaScript" and only the administration's explicit approval releases it
(CSP with the hash + load with SRI; a new hash = a new approval).

The file lives in the package at ``ui/assets/<name>.js`` and the app serves it at
``/{id}/assets/<name>.js`` with **the same bytes** (otherwise the browser refuses it via SRI).
After each fragment swap the hub fires ``hub:mount`` on the ``document``.

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
    """Entry of ``manifest.scripts`` for ``ui/assets/<file>`` (same rules as the hub)."""
    if not _NAME.match(file) or file.startswith(".") or ".." in file or len(file) < 4:
        raise ValueError(f"nome de script inválido: {file!r} (use <nome>.js, minúsculas)")
    text = reason.strip()
    if not 10 <= len(text) <= 500 or any(ord(c) < 32 or ord(c) == 127 for c in reason):
        raise ValueError("motivo com 10–500 caracteres é obrigatório")
    return {"path": f"ui/assets/{file}", "reason": text}


def integrity(data: bytes) -> str:
    """SRI/CSP integrity (``sha384-<base64>``) — the same one the hub computes on import."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
