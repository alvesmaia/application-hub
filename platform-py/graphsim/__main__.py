"""`python -m graphsim` (porta `PORT`, padrão 8080); `--health` sonda `/_health` (HEALTHCHECK sem curl)."""

import os
import sys
import urllib.request

import uvicorn

from graphsim.sim import Sim, SimConfig, make_app

port = int(os.environ.get("PORT", "8080"))
if len(sys.argv) > 1 and sys.argv[1] == "--health":
    try:
        ok = urllib.request.urlopen(f"http://127.0.0.1:{port}/_health", timeout=3).status == 200
    except OSError:
        ok = False
    sys.exit(0 if ok else 1)
cfg = SimConfig.from_env()
print(
    f"graphsim: SIMULADOR do Microsoft Graph (só testes/demonstração) em :{port}, tenant {cfg.tenant}",
    flush=True,
)
uvicorn.run(make_app(Sim(cfg)), host="0.0.0.0", port=port, log_level="warning")  # noqa: S104
