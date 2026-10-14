"""Entra ID token endpoint of the simulator (client secret or RS256 certificate assertion)."""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

from starlette.responses import JSONResponse, Response

from graphsim.common import aad, verify_assertion

if TYPE_CHECKING:
    from graphsim.sim import Sim


def token(sim: Sim, path: str, body: bytes) -> Response:
    sim.stats["token_requests"] += 1
    cfg = sim.cfg
    tenant = path.lstrip("/").split("/")[0]
    f = dict(parse_qsl(body.decode("utf-8", "replace")))
    if tenant != cfg.tenant:
        return aad(400, "invalid_request", f"AADSTS90002: Tenant '{tenant}' not found.")
    if f.get("grant_type") != "client_credentials":
        return aad(400, "unsupported_grant_type", "AADSTS70003: unsupported grant type")
    if f.get("scope") != "https://graph.microsoft.com/.default":
        return aad(400, "invalid_scope", "AADSTS70011: invalid scope")
    cid = f.get("client_id", "")
    if cid != cfg.client_id:
        return aad(
            400,
            "unauthorized_client",
            f"AADSTS700016: Application with identifier '{cid}' was not found in the directory.",
        )
    ok = False
    if "client_secret" in f:
        ok = bool(cfg.client_secret) and secrets.compare_digest(f["client_secret"], cfg.client_secret)
    elif "client_assertion" in f:
        sim.stats["assertions"] += 1
        cl = verify_assertion(f["client_assertion"], cfg.cert_pem)
        now = time.time()
        ok = bool(
            cl
            and cl.get("iss") == cid
            and cl.get("sub") == cid
            and str(cl.get("aud", "")).endswith(f"/{tenant}/oauth2/v2.0/token")
            and cl.get("exp", 0) > now
            and cl.get("nbf", 0) <= now + 60
        )
    if not ok:
        return aad(401, "invalid_client", "AADSTS7000215: Invalid client secret or assertion provided.")
    tok = "simtok-" + secrets.token_hex(16)
    sim.tokens[tok] = time.monotonic() + cfg.token_ttl_s
    sim.stats["tokens_issued"] += 1
    t = cfg.token_ttl_s
    return JSONResponse({"token_type": "Bearer", "expires_in": t, "ext_expires_in": t, "access_token": tok})
