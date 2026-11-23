"""``python -m automations [serve|openapi|--health]``.

- ``serve`` (default): uvicorn on ``AUTOMATIONS_HOST`` (default 127.0.0.1; the image uses 0.0.0.0) and
  ``PORT`` (default 8080), with ``UVICORN_WORKERS`` processes and 8 s of drain on SIGTERM. The
  durable jobs (short HTTP calls) run inline in the web process (``HUB_JOBS_MODE=inline``) and each
  process runs the scheduler loop (fires are exactly-once across processes).
- ``openapi``: prints the OpenAPI of API v1 (``hub pack`` embeds it as ``openapi.json``).
- ``--health``: ``GET /automations`` on the app itself (image HEALTHCHECK, no curl); exits 0 with 200.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import sys


def health() -> int:
    port = int(os.environ.get("PORT", "8080"))
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/automations")
        ok = c.getresponse().status == 200
        c.close()
    except OSError:
        ok = False
    return 0 if ok else 1


def serve() -> int:
    import uvicorn

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.environ.get("AUTOMATIONS_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    workers = max(1, int(os.environ.get("UVICORN_WORKERS", "1")))
    print(f"automations: http://{host}:{port}/automations ({workers} worker(s) web)", flush=True)
    uvicorn.run(
        "automations.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        timeout_graceful_shutdown=8,
        server_header=False,
        access_log=False,
        log_level="warning",
        lifespan="on",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"
    if cmd == "--health":
        return health()
    if cmd == "openapi":
        from automations.app import create_app

        doc = json.dumps(create_app().state.hub_api_doc(), ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.buffer.write(doc.encode("utf-8") + b"\n")
        return 0
    if cmd == "serve":
        return serve()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
