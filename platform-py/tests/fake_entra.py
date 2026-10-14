"""Fake OIDC provider in the Entra ID v2 format (authorize/token/JWKS) for tests and a temporary instance.

Standalone use: `uv run python tests/fake_entra.py` (port 8095; FAKE_ENTRA_ISSUER = base that the hub uses as
HUB_ENTRA_AUTHORITY). The authorize approves at once and returns the code to the redirect_uri.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.hashes import SHA256
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "client-123"
SECRET = "fake-secret"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# Per-test adjustments: extra/replacement claims and `bad_sig`/`bad_nonce`.
OVERRIDES: dict[str, Any] = {}
_codes: dict[str, str] = {}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def id_token(issuer_base: str, nonce: str) -> str:
    o = dict(OVERRIDES)
    tid = o.pop("tid", TENANT)
    now = int(time.time())
    claims = {
        "iss": f"{issuer_base}/{tid}/v2.0",
        "aud": CLIENT,
        "tid": tid,
        "oid": "oid-ana",
        "sub": "sub-ana",
        "name": "Ana Entra",
        "preferred_username": "ana@contoso.com",
        "email": "ana@contoso.com",
        "nonce": "outro" if o.pop("bad_nonce", False) else nonce,
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    key = OTHER if o.pop("bad_sig", False) else KEY
    claims |= o
    h = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "k1"}).encode())
    c = _b64(json.dumps(claims).encode())
    sig = key.sign(f"{h}.{c}".encode(), PKCS1v15(), SHA256())
    return f"{h}.{c}.{_b64(sig)}"


def make_app(issuer_base: str) -> FastAPI:
    app = FastAPI()

    @app.get("/{tenant}/oauth2/v2.0/authorize")
    async def authorize(request: Request) -> RedirectResponse:
        q = request.query_params
        code = secrets.token_urlsafe(16)
        _codes[code] = q["nonce"]
        sep = "&" if "?" in q["redirect_uri"] else "?"
        return RedirectResponse(f"{q['redirect_uri']}{sep}code={code}&state={q['state']}", status_code=302)

    @app.post("/{tenant}/oauth2/v2.0/token")
    async def token(
        code: str = Form(...), client_secret: str = Form(...), code_verifier: str = Form(...)
    ) -> JSONResponse:
        nonce = _codes.pop(code, None)
        if nonce is None or client_secret != SECRET or not code_verifier:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse({"id_token": id_token(issuer_base, nonce), "token_type": "Bearer"})

    @app.get("/{tenant}/discovery/v2.0/keys")
    async def keys() -> dict[str, Any]:
        n = KEY.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "n": _int(n.n), "e": _int(n.e)}]}

    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("FAKE_ENTRA_PORT", "8095"))
    uvicorn.run(
        make_app(os.getenv("FAKE_ENTRA_ISSUER", f"http://127.0.0.1:{port}")),
        host=os.getenv("FAKE_ENTRA_HOST", "127.0.0.1"),
        port=port,
    )
