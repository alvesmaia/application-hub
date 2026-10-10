import base64
import json

from hub_sdk.identity import GUEST, Identity, parse_identity


def enc(d: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_parses_full_and_legacy_formats() -> None:
    full = parse_identity(
        enc(
            {
                "user": "ana@corp.com",
                "roles": ["Administrador"],
                "id": "u1",
                "name": "Ana",
                "email": "ana@corp.com",
                "permissions": ["acessar_aplicacoes"],
            }
        )
    )
    assert full is not None
    assert full.display_name == "Ana"
    assert full.has_permission("acessar_aplicacoes")
    legacy = parse_identity(enc({"user": "bia", "roles": []}))
    assert legacy is not None and legacy.display_name == "bia" and legacy.permissions == ()


def test_invalid_or_missing_is_none() -> None:
    for bad in [None, "", "%%%", enc([1, 2]), enc({"roles": []}), enc({"user": ""}), "e30"]:
        assert parse_identity(bad) is None, bad


def test_roundtrip_matches_gateway_encoding() -> None:
    i = Identity(user="ana@corp.com", roles=("Leitor",), name="Ana")
    v = i.encode()
    assert "=" not in v
    assert json.loads(base64.urlsafe_b64decode(v + "==")) == {
        "user": "ana@corp.com",
        "roles": ["Leitor"],
        "name": "Ana",
    }
    assert parse_identity(v) == i
    assert Identity(user=GUEST).is_guest
