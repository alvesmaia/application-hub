"""`python -m hub.admin`: initial administrator with a temporary password and deactivation of the demo accounts."""

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


def test_add_sharepoint_connection_from_the_installer(tmp_path: Path, monkeypatch, capsys) -> None:
    import io

    import pytest

    from hub.admin.store import AdminStore
    from hub.connections.store import ConnStore
    from hub.vault import Vault

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    args = ["add-sharepoint", "--name", "backups-sharepoint", "--tenant", "7b369f6a-86a3-4b85-9b78-ac3bdf47a74d",
            "--client-id", "DABA04DC-E352-4C55-8815-A19BD702B569", "--site", "https://contoso.sharepoint.com/sites/TI"]  # fmt: skip
    monkeypatch.setattr("sys.stdin", io.StringIO("s3gredo-sp\n"))
    assert main(args) == 0
    vault = Vault.open(tmp_path)
    c = ConnStore(tmp_path, vault, AdminStore(tmp_path, vault, apps=[])).get("backups-sharepoint")
    assert (
        c
        and c["kind"] == "sharepoint"
        and c["secret"] == "s3gredo-sp"
        and c["client_id"].startswith("daba04dc")
    )
    assert "s3gredo" not in "".join(p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file())
    assert "s3gredo" not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("s3gredo-novo\n"))
    assert main(args) == 0  # running the installer again updates the same connection
    vault = Vault.open(tmp_path)
    c = ConnStore(tmp_path, vault, AdminStore(tmp_path, vault, apps=[])).get("backups-sharepoint")
    assert c and c["secret"] == "s3gredo-novo"
    monkeypatch.setattr("sys.stdin", io.StringIO("x\n"))
    with pytest.raises(SystemExit):
        main([*args[:-1], "http://nao-e-sharepoint"])
