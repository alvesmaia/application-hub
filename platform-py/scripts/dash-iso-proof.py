"""Proof for the dashboard kit on the isolated instance (`scripts/iso-instance.sh up`): creates the PostgreSQL connection
`vendas` (sample database in the iso app-db), tests it, creates a CI token and writes it to `$TEMP/dash-proof/token`.
After `hub deploy --env staging`, run with `link` to link the connection to the app in staging.
Usage: uv run python scripts/dash-iso-proof.py setup|link|check"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

HUB = "http://127.0.0.1:8093"
OUT = Path(os.environ["TEMP"]) / "dash-proof"


def client() -> httpx.Client:
    env = dict(
        line.split("=", 1) for line in Path(".iso-instance.env").read_text().splitlines() if "=" in line
    )
    c = httpx.Client(base_url=HUB, headers={"origin": HUB}, timeout=60)
    r = c.post(
        "/login", data={"email": "admin@demo.local", "password": env["HUB_DEMO_PASSWORD"], "next": "/"}
    )
    if r.status_code not in (200, 303):
        raise SystemExit(f"login: HTTP {r.status_code}")
    return c


def setup(c: httpx.Client) -> None:
    body = {
        "name": "vendas",
        "kind": "postgres",
        "description": "Vendas (exemplo do kit de dashboards)",
        "host": "app-hub-app-db",
        "port": 5432,
        "database": "vendas_src",
        "username": "vendas_leitor",
        "secret": os.environ["VENDAS_PW"],
        "ssl": "disable",
        "bindings": [],
    }
    r = c.post("/api/admin/connections", json=body)
    print("criar", r.status_code, r.text[:200])
    t = c.post("/api/admin/connections/vendas/test").json()
    print("teste", t["ok"], [(s["key"], s["ok"], s["detail"][:40]) for s in t["steps"]])
    tok = c.post(
        "/api/admin/tokens",
        json={"name": "dash-proof", "apps": [], "actions": ["upload", "status"], "expires_days": 1},
    )
    print("token", tok.status_code)
    OUT.mkdir(exist_ok=True)
    (OUT / "token").write_text(tok.json()["token"], encoding="utf-8")


def link(c: httpx.Client) -> None:
    r = c.put(
        "/api/admin/connections/vendas/bindings",
        json={"bindings": [{"app": "vendas-bi", "env": "staging"}]},
    )
    print("vínculo", r.status_code)
    print("aplicar", c.post("/api/ops/runtime/vendas-bi/apply?env=staging", json={}).status_code)


def check(c: httpx.Client) -> None:
    c.get("/hub-env?to=staging&next=/apps/vendas-bi")
    r = c.get("/_frag/vendas-bi/painel")
    print("painel", r.status_code, "kpis" if "hub-dash-kpi" in r.text else r.text[-300:])
    audit = c.get("/api/admin/audit").text
    print(
        "auditoria Consulta SQL:",
        "Consulta SQL" in audit,
        "senha ausente:",
        os.environ["VENDAS_PW"] not in audit,
    )


COMMANDS = {"setup": setup, "link": link, "check": check}


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(
            f"usage: uv run python scripts/dash-iso-proof.py setup|link|check (got {argv[1:]!r})",
            file=sys.stderr,
        )
        sys.exit(2)
    COMMANDS[argv[1]](client())


if __name__ == "__main__":
    main(sys.argv)
