import json
import os
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from hub.vault import CHUNK, Vault, is_sealed, verify_signature

ROOT = Path(__file__).resolve().parents[2]


def test_rust_ciphertext_decrypts_in_python(tmp_path: Path) -> None:
    vector = json.loads((Path(__file__).parent / "vectors/rust-vault-v2.json").read_text())
    key = tmp_path / "master.key"
    key.write_text(vector["master_key"], encoding="ascii")
    data = tmp_path / "data"
    data.mkdir()
    (data / "keyring.json").write_text(json.dumps(vector["keyring"]), encoding="utf-8")
    vault = Vault.open(data, key)
    assert vault.open_value(vector["ciphertext"], vector["aad"]) == vector["plaintext"]


def test_interrupted_master_key_rotation_and_env_key_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "master.key"
    key.write_text("55" * 32, encoding="ascii")
    data = tmp_path / "data"
    vault = Vault.open(data, key)
    vault.set_protected("test", "preserved")
    pending = key.with_suffix(".key.new")
    from hub.vault import write_key_file

    write_key_file(pending, b"\x66" * 32)
    vault.rotate_master_key(b"\x66" * 32)
    reopened = Vault.open(data, key)
    assert not pending.exists()
    assert reopened.protected("test") == "preserved"
    monkeypatch.setenv("HUB_MASTER_KEY_FILE", str(key))
    assert Vault.open(tmp_path / "env-data").key_file == key


def test_keyring_rotation_protected_metadata_and_signatures(tmp_path: Path) -> None:
    vault = Vault.open(tmp_path)
    sealed = vault.seal("secret", "settings:token")
    assert is_sealed(sealed)
    assert vault.open_value(sealed, "settings:token") == "secret"
    with pytest.raises(ValueError):
        vault.open_value(sealed, "elsewhere")
    vault.set_protected("audit.anchor", "seq=7")
    old_id = vault.active_dek
    assert vault.rotate_data_key() != old_id
    assert vault.open_value(sealed, "settings:token") == "secret"
    vault.rotate_master_key_file(b"\x22" * 32)
    reopened = Vault.open(tmp_path)
    assert reopened.protected("audit.anchor") == "seq=7"
    sig = reopened.sign(b"checkpoint")
    assert verify_signature(reopened.signing_public_key, b"checkpoint", sig)
    assert not verify_signature(reopened.signing_public_key, b"tampered", sig)
    assert json.loads((tmp_path / "keyring.json").read_text())["version"] == 2


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1])
def test_streaming_file_round_trip(tmp_path: Path, size: int) -> None:
    vault = Vault.in_memory(b"\x33" * 32)
    src, encrypted, output = (tmp_path / name for name in ("plain", "sealed", "opened"))
    source = os.urandom(size)
    src.write_bytes(source)
    assert vault.encrypt_file(src, encrypted, "backup/x") == size
    assert vault.decrypt_file(encrypted, output, "backup/x") == size
    assert output.read_bytes() == source
    with pytest.raises(InvalidTag):
        vault.decrypt_file(encrypted, output, "backup/y")
