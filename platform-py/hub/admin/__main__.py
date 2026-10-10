"""Operação offline: `python -m hub.admin create-admin --email E --out ARQUIVO` e `deactivate-demo`.

Rode com o hub parado (escreve `auth.json` direto). Nada secreto vai para a saída: a senha temporária
vai só para o arquivo `--out` (0600). O usuário é obrigado a trocar a senha no primeiro acesso.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

from hub.auth import PH, AuthStore, normalize_email, now_ms, policy

_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+="


async def create_admin(store: AuthStore, email: str, name: str) -> str:
    """Cria/reinicia o administrador e devolve o texto do arquivo de primeiro acesso."""
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hub.admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="cria (ou reinicia) um administrador com senha temporária")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Administrador")
    c.add_argument("--out", required=True, type=Path, help="arquivo (0600) que recebe a senha temporária")
    sub.add_parser(
        "deactivate-demo", help="desativa contas @demo.local (mantém a auditoria) e revoga tokens ativos"
    )
    a = p.parse_args(argv)
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
