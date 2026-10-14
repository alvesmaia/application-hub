"""``python -m __PKG__ [serve|--health|openapi]``.

- ``serve`` (default): uvicorn on ``HOST`` (127.0.0.1; the image uses 0.0.0.0) and ``PORT`` (8080).
- ``--health``: ``GET /__APP_ID__/_hub/ready`` on the app itself (the image's HEALTHCHECK); exits 0 on 200.
- ``openapi``: prints the API v1 OpenAPI document (``hub pack`` embeds it as openapi.json).
"""

from __future__ import annotations

import http.client
import json
import os
import sys


def health() -> int:
    try:
        c = http.client.HTTPConnection("127.0.0.1", int(os.environ.get("PORT", "8080")), timeout=3)
        c.request("GET", "/__APP_ID__/_hub/ready")
        ok = c.getresponse().status == 200
        c.close()
    except OSError:
        return 1
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"
    if cmd == "--health":
        return health()
    if cmd == "openapi":
        from __PKG__.app import create_app

        doc = create_app().state.hub_api_doc()
        text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.buffer.write(text.encode() + b"\n")
        return 0
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(
        "__PKG__.app:create_app",
        factory=True,
        host=host,
        port=int(os.environ.get("PORT", "8080")),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
