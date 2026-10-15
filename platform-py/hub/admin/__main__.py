"""Offline operation: `python -m hub.admin create-admin --email E --out ARQUIVO`, `deactivate-demo` and
`set-entra-secret` (reads the Entra ID client secret from stdin and stores it in the vault).

Run with the hub stopped (writes `auth.json` directly). Nothing secret goes to the output: the temporary password
goes only to the `--out` file (0600). The user must change the password on first access.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from pathlib import Path

from hub.auth import PH, AuthStore, entra, normalize_email, now_ms, policy
from hub.vault import Vault

_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+="


async def create_admin(store: AuthStore, email: str, name: str) -> str:
    """Creates/resets the administrator and returns the text of the first-access file."""
    email = normalize_email(email)
    password = ""
    while policy(password):
        password = "".join(secrets.choice(_ALPHABET) for _ in range(20))
    async with store.locked(True) as d:
        user = next((u for u in d["users"] if u["email"] == email), None)
        if user is None:
            d["users"].append(store._new_user(d, email, name, "Administrador", password, True))
        else:
            user.update(
                active=True,
                password_hash=PH.hash(password),
                must_change_password=True,
                failed_attempts=0,
                locked_until=None,
                updated_at=now_ms(),
            )
            if "Administrador" not in user["roles"]:
                user["roles"] = [*user["roles"], "Administrador"]
    return f"Primeiro acesso\nE-mail: {email}\nSenha temporária: {password}\n\nTroque a senha no primeiro acesso.\n"


async def deactivate_demo(store: AuthStore) -> int:
    n = 0
    async with store.locked(True) as d:
        for u in d["users"]:
            if u["email"].endswith("@demo.local") and u.get("active", True):
                u.update(active=False, updated_at=now_ms())
                u.pop("password_hash", None)
                n += 1
        for t in d["tokens"]:
            if t.get("revoked_at") is None:
                t["revoked_at"] = now_ms()
        d["sessions"] = []
    return n


def add_sharepoint(name: str, tenant: str, client_id: str, site: str) -> int:
    """Used by `scripts/install.sh`: the connection goes to the store and the secret to the vault (never argv)."""
    from hub.admin.store import AdminStore
    from hub.connections.store import ConnError, ConnStore

    secret = (getpass.getpass("Segredo do cliente: ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    data = Path(os.getenv("HUB_DATA_DIR", "/data"))
    vault = Vault.open(data)
    store = ConnStore(data, vault, AdminStore(data, vault, apps=[]))
    i = {"name": name, "kind": "sharepoint", "tenant_id": tenant, "client_id": client_id, "site": site,
         "auth": "secret", "secret": secret}  # fmt: skip
    try:
        asyncio.run(store.update(name, i) if store.get(name) else store.create(i))
    except ConnError as e:
        raise SystemExit(f"conexão SharePoint: {e}") from e
    print(f"conexão SharePoint {name} pronta; teste em Administração › Conexões")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m hub.admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="cria (ou reinicia) um administrador com senha temporária")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Administrador")
    c.add_argument("--out", required=True, type=Path, help="arquivo (0600) que recebe a senha temporária")
    sub.add_parser(
        "deactivate-demo", help="desativa contas @demo.local (mantém a auditoria) e revoga tokens ativos"
    )
    sub.add_parser(
        "set-entra-secret", help="guarda no cofre o segredo do cliente do Entra ID (entrada padrão)"
    )
    sp = sub.add_parser(
        "add-sharepoint", help="cria ou atualiza uma conexão SharePoint (segredo pela entrada padrão)"
    )
    sp.add_argument("--name", required=True)
    sp.add_argument("--tenant", required=True)
    sp.add_argument("--client-id", required=True)
    sp.add_argument("--site", required=True)
    return p


def set_entra_secret() -> int:
    secret = (getpass.getpass("Segredo do cliente: ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    if not secret:
        print("segredo vazio; nada foi gravado", file=sys.stderr)
        return 1
    Vault.open(Path(os.getenv("HUB_DATA_DIR", "/data"))).set_protected(entra.VAULT_SECRET, secret)
    print("segredo do Entra ID guardado no cofre; reinicie o hub")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "add-sharepoint":
        return add_sharepoint(a.name, a.tenant, a.client_id, a.site)
    if a.cmd == "set-entra-secret":
        return set_entra_secret()
    store = AuthStore(Path(os.getenv("HUB_DATA_DIR", "/data")) / "auth.json")
    if a.cmd == "create-admin":
        text = asyncio.run(create_admin(store, a.email, a.name))
        a.out.write_text(text, encoding="utf-8")
        a.out.chmod(0o600)
        print(f"administrador {normalize_email(a.email)} pronto; senha temporária em {a.out}")
    else:
        print(f"{asyncio.run(deactivate_demo(store))} conta(s) demo desativada(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
