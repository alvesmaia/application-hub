"""`python -m hub.admin`: administrador inicial com senha temporária e desativação das contas demo."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hub.admin.__main__ import main
from hub.auth import AuthStore, verify


def test_create_admin_and_deactivate_demo(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_DEMO_USERS", "1")
    asyncio.run(AuthStore(tmp_path / "auth.json").bootstrap())
    monkeypatch.delenv("HUB_DEMO_USERS")
    out = tmp_path / "primeiro-acesso.txt"
    assert main(["create-admin", "--email", "Dono@Exemplo.com", "--out", str(out)]) == 0
    assert main(["deactivate-demo"]) == 0
    text = out.read_text(encoding="utf-8")
    password = text.split("Senha temporária: ")[1].splitlines()[0]
    assert password not in capsys.readouterr().out
    users = {u["email"]: u for u in json.loads((tmp_path / "auth.json").read_text())["users"]}
    admin = users["dono@exemplo.com"]
    assert admin["active"] and admin["must_change_password"] and "Administrador" in admin["roles"]
    assert verify(password, admin["password_hash"])
    demos = [u for e, u in users.items() if e.endswith("@demo.local")]
    assert demos and not any(u["active"] or u.get("password_hash") for u in demos)
