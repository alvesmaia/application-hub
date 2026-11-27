"""The app's own files for the browser (fonts, images) — adapting an existing app keeps them.

``asset_file(folder, name)`` answers one file of ``folder`` by **name**: no client path reaches the disk
(no ``/``, no ``..``), only font and image types, ``nosniff``. CSS stays on its closed list (``app.css``).
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.responses import Response

TYPES = {
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


def asset_file(folder: Path, name: str) -> Response:
    """One font/image of ``folder`` by name, or 404 (bad name, other type, missing file)."""
    kind = TYPES.get(Path(name).suffix.lower())
    path = folder / name
    if not (_NAME.fullmatch(name) and ".." not in name and kind is not None and path.is_file()):
        return Response("not found", status_code=404, media_type="text/plain")
    headers = {"cache-control": "public, max-age=86400", "x-content-type-options": "nosniff"}
    return Response(path.read_bytes(), media_type=kind, headers=headers)
