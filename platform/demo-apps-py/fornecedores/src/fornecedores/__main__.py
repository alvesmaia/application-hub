"""``python -m fornecedores [serve|openapi|--health]``.

- ``serve`` (padrão): uvicorn em ``FORNECEDORES_HOST`` (padrão 127.0.0.1; a imagem usa 0.0.0.0) e
  ``PORT`` (padrão 8080), com ``UVICORN_WORKERS`` processos e 8 s de drenagem no SIGTERM. O app
  não tem tarefas duráveis nem esquema próprio (só o outbox do SDK, se houver ``HUB_DB_URL``).
- ``openapi``: imprime o OpenAPI da API v1 (o ``hub pack`` embute como ``openapi.json``).
- ``--health``: ``GET /fornecedores`` no próprio app (HEALTHCHECK da imagem, sem curl); sai 0 com 200.
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
        c.request("GET", "/fornecedores")
        ok = c.getresponse().status == 200
        c.close()
    except OSError:
        ok = False
    return 0 if ok else 1


def serve() -> int:
    import uvicorn

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host = os.environ.get("FORNECEDORES_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    workers = max(1, int(os.environ.get("UVICORN_WORKERS", "1")))
    print(f"fornecedores: http://{host}:{port}/fornecedores ({workers} worker(s) web)", flush=True)
    uvicorn.run(
        "fornecedores.app:create_app",
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
        from fornecedores.app import create_app

        doc = json.dumps(create_app().state.hub_api_doc(), ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.buffer.write(doc.encode("utf-8") + b"\n")
        return 0
    if cmd == "serve":
        return serve()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
