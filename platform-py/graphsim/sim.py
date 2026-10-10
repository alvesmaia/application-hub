"""Simulador do Graph e do login do Entra ID (porte do `hub-graphsim` do Rust).

Rotas: token (segredo ou asserção RS256 do certificado configurado), site/bibliotecas, item/exclusão/
mover, download (302 para URL pré-autenticada), upload simples e em sessão, listagem paginada, pasta,
link de compartilhamento e `/_sim/*` (config, stats, reset). Arquivos só em memória.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, unquote

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

CHUNK_UNIT = 320 * 1024
SIMPLE_MAX = 4 * 1024 * 1024


@dataclass
class SimConfig:
    tenant: str = "00000000-0000-0000-0000-00000000c0de"
    client_id: str = "11111111-2222-3333-4444-555555555555"
    client_secret: str = "segredo-do-simulador"  # noqa: S105
    cert_pem: str = ""
    hostname: str = "contoso.sharepoint.com"
    site_path: str = "sites/Hub"
    site_name: str = "Hub"
    drives: list[str] = field(default_factory=lambda: ["Documentos", "Arquivos"])
    token_ttl_s: int = 3600
    throttle_every: int = 0
    throttle_next: int = 0
    throttle_retry_after: int = 1
    fail_rate: float = 0.0
    latency_ms: int = 0
    max_bytes: int = 512 * 1024 * 1024
    control: bool = True
    deny_site: bool = False
    link_expiry: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SimConfig:
        e = os.environ if env is None else env
        c = cls()
        s = lambda k: e.get(k) or None  # noqa: E731
        for attr, key in (
            ("tenant", "TENANT"),
            ("client_id", "CLIENT_ID"),
            ("client_secret", "CLIENT_SECRET"),
            ("hostname", "HOSTNAME"),
            ("site_path", "SITE_PATH"),
        ):
            if v := s(f"GRAPHSIM_{key}"):
                setattr(c, attr, v)
        if v := s("GRAPHSIM_CERT_PEM_FILE"):
            try:
                with open(v, encoding="utf-8") as fh:
                    c.cert_pem = fh.read()
            except OSError:
                c.cert_pem = ""
        if v := s("GRAPHSIM_DRIVES"):
            c.drives = [d.strip() for d in v.split(",")]
        for attr, key in (
            ("token_ttl_s", "TOKEN_TTL_S"),
            ("throttle_every", "THROTTLE_EVERY"),
            ("throttle_retry_after", "THROTTLE_RETRY_AFTER"),
            ("latency_ms", "LATENCY_MS"),
        ):
            if (v := s(f"GRAPHSIM_{key}")) and v.isdigit():
                setattr(c, attr, int(v))
        if (v := s("GRAPHSIM_MAX_MB")) and v.isdigit():
            c.max_bytes = int(v) << 20
        if v := s("GRAPHSIM_FAIL_RATE"):
            c.fail_rate = float(v)
        c.control = s("GRAPHSIM_CONTROL") != "0"
        return c

    def site_id(self) -> str:
        return f"{self.hostname},6f0d1e2a-0000-4000-8000-000000000001,6f0d1e2a-0000-4000-8000-000000000002"


def drive_id(name: str) -> str:
    return "b!sim_" + re.sub(r"[^A-Za-z0-9]", "_", name)


def rfc3339(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_assertion(jwt: str, pem: str) -> dict[str, Any] | None:
    m = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    if not m:
        return None
    try:
        cert = x509.load_pem_x509_certificate(m.group().encode())
        der = cert.public_bytes(serialization.Encoding.DER)
        si, sig = jwt.rsplit(".", 1)
        h, c = si.split(".", 1)
        pub = cert.public_key()
        if not isinstance(pub, rsa.RSAPublicKey):
            return None
        pub.verify(_b64u_dec(sig), si.encode(), padding.PKCS1v15(), hashes.SHA256())
        want = base64.urlsafe_b64encode(hashlib.sha1(der).digest()).rstrip(b"=").decode()  # noqa: S324
        if json.loads(_b64u_dec(h)).get("x5t") != want:
            return None
        claims = json.loads(_b64u_dec(c))
    except (ValueError, InvalidSignature, KeyError):
        return None
    return claims if isinstance(claims, dict) else None


def gerr(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": msg}}, status_code=status)


def aad(status: int, err: str, desc: str) -> JSONResponse:
    return JSONResponse({"error": err, "error_description": desc}, status_code=status)


NOT_FOUND = ("itemNotFound", "The resource could not be found.")


def base_url(req: Request) -> str:
    return f"http://{req.headers.get('host', 'localhost')}"


class Sim:
    def __init__(self, cfg: SimConfig | None = None) -> None:
        self.cfg = cfg or SimConfig()
        self.reset()

    def reset(self) -> None:
        self.stats: dict[str, int] = dict.fromkeys(
            [
                "requests",
                "graph_calls",
                "throttled",
                "failed",
                "token_requests",
                "tokens_issued",
                "assertions",
                "sessions",
                "chunks",
                "simple_uploads",
                "downloads",
                "downloads_with_auth",
                "unauthorized",
            ],
            0,
        )
        self.drives: dict[str, dict[str, dict[str, Any]]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.downloads: dict[str, tuple[str, str]] = {}
        self.tokens: dict[str, float] = {}
        self.links: dict[str, str] = {}
        self.bytes = 0
        self.seq = 0
        for name in self.cfg.drives:
            did = drive_id(name)
            self.drives[did] = {"": self._node(f"root-{did}", "root", True)}

    @staticmethod
    def _node(
        id_: str, name: str, folder: bool, data: bytes = b"", ctype: str = "", version: int = 1
    ) -> dict[str, Any]:
        return {
            "id": id_,
            "name": name,
            "folder": folder,
            "data": data,
            "ctype": ctype,
            "modified": rfc3339(),
            "version": version,
        }

    def _next_id(self) -> str:
        self.seq += 1
        return f"itm-{self.seq:06d}"

    # ---- testes ----------------------------------------------------------------------------
    def file(self, drive: str, path: str) -> bytes | None:
        n = self.drives.get(drive_id(drive), {}).get(path.strip("/"))
        return None if n is None or n["folder"] else bytes(n["data"])

    def paths(self, drive: str) -> list[str]:
        return [k for k in self.drives.get(drive_id(drive), {}) if k]

    # ---- entrada ---------------------------------------------------------------------------
    async def handle(self, req: Request) -> Response:
        path, method = req.url.path, req.method
        body = await req.body()
        self.stats["requests"] += 1
        if path.startswith("/_sim/"):
            return self.control(method, path[6:], body)
        if path == "/_health":
            return Response("ok")
        if self.cfg.latency_ms:
            await asyncio.sleep(self.cfg.latency_ms / 1000)
        if r := self.throttle():
            return r
        if path.endswith("/oauth2/v2.0/token") and method == "POST":
            return self.token(path, body)
        if path.startswith("/_upload/"):
            return self.upload_session(method, path[9:], req, body)
        if path.startswith("/_dl/"):
            return self.download_url(path[5:], req)
        if not path.startswith("/v1.0/"):
            return gerr(404, "itemNotFound", "rota desconhecida")
        auth = req.headers.get("authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else ""
        if self.tokens.get(tok, 0) <= time.monotonic():
            self.stats["unauthorized"] += 1
            return gerr(401, "InvalidAuthenticationToken", "Access token is empty or invalid.")
        self.stats["graph_calls"] += 1
        return self.graph(method, path[6:], dict(parse_qsl(req.url.query)), req, body)

    def throttle(self) -> Response | None:
        c = self.cfg
        if c.throttle_next > 0:
            c.throttle_next -= 1
            hit = True
        else:
            hit = c.throttle_every > 0 and self.stats["requests"] % c.throttle_every == 0
        if hit:
            self.stats["throttled"] += 1
            r = gerr(429, "activityLimitReached", "The request has been throttled")
            r.headers["retry-after"] = str(c.throttle_retry_after)
            return r
        if c.fail_rate > 0 and random.random() < c.fail_rate:  # noqa: S311
            self.stats["failed"] += 1
            return gerr(503, "serviceNotAvailable", "Service unavailable")
        return None

    def control(self, method: str, rest: str, body: bytes) -> Response:
        if not self.cfg.control:
            return Response(status_code=404)
        match (method, rest):
            case ("GET", "stats"):
                return JSONResponse(self.stats)
            case ("GET", "config"):
                return JSONResponse(asdict(self.cfg))
            case ("GET", "health"):
                return Response("ok")
            case ("POST", "config"):
                try:
                    self.cfg = SimConfig(**{**asdict(self.cfg), **json.loads(body)})
                except (ValueError, TypeError):
                    return Response(status_code=400)
                return JSONResponse(asdict(self.cfg))
            case ("POST", "reset"):
                self.reset()
                return Response(status_code=204)
        return Response(status_code=404)

    def token(self, path: str, body: bytes) -> Response:
        self.stats["token_requests"] += 1
        cfg = self.cfg
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
            self.stats["assertions"] += 1
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
        self.tokens[tok] = time.monotonic() + cfg.token_ttl_s
        self.stats["tokens_issued"] += 1
        t = cfg.token_ttl_s
        return JSONResponse(
            {"token_type": "Bearer", "expires_in": t, "ext_expires_in": t, "access_token": tok}
        )

    # ---- Graph -----------------------------------------------------------------------------
    @staticmethod
    def item_json(drive: str, path: str, n: dict[str, Any]) -> dict[str, Any]:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        v: dict[str, Any] = {
            "id": n["id"],
            "name": n["name"],
            "size": 0 if n["folder"] else len(n["data"]),
            "lastModifiedDateTime": n["modified"],
            "eTag": f'"{{{n["id"]}}},{n["version"]}"',
            "parentReference": {"driveId": drive, "path": f"/drives/{drive}/root:/{parent}"},
        }
        if n["folder"]:
            v["folder"] = {"childCount": 0}
        else:
            v["file"] = {"mimeType": n["ctype"]}
        return v

    def parse_drive_path(self, rest: str) -> tuple[str, str, str] | None:
        if "/" not in rest:
            return None
        drive, tail = rest.split("/", 1)
        drive = unquote(drive)
        if tail.startswith("root:"):
            t = tail[5:].lstrip("/")
            i = t.rfind(":/")
            p, suffix = (t[:i], t[i + 2 :]) if i >= 0 else (t.rstrip(":"), "")
            return drive, unquote(p).strip("/"), suffix
        if tail == "root":
            return drive, "", ""
        if tail.startswith("root/"):
            return drive, "", tail[5:]
        if tail.startswith("items/"):
            iid, _, suffix = tail[6:].partition("/")
            iid = unquote(iid)
            for p, n in self.drives.get(drive, {}).items():
                if n["id"] == iid:
                    return drive, p, suffix
        return None

    def site(self, g: str) -> Response:
        cfg = self.cfg
        if cfg.deny_site:
            return gerr(403, "accessDenied", "Access denied")
        s = unquote(g[6:])
        ref, sid = f"{cfg.hostname}:/{cfg.site_path}", cfg.site_id()
        sub = s[len(ref) :] if s.startswith(ref) else s[len(sid) :] if s.startswith(sid) else None
        if sub is None:
            return gerr(404, "itemNotFound", "Requested site could not be found")
        sub = sub.lstrip("/")
        drives = [{"id": drive_id(d), "name": d, "driveType": "documentLibrary"} for d in cfg.drives]
        if sub == "":
            return JSONResponse(
                {
                    "id": sid,
                    "name": cfg.site_path.rsplit("/", 1)[-1],
                    "displayName": cfg.site_name,
                    "webUrl": f"https://{cfg.hostname}/{cfg.site_path}",
                }
            )
        if sub == "drives":
            return JSONResponse({"value": drives})
        if sub == "drive":
            return JSONResponse(drives[0] if drives else None)
        return gerr(404, "itemNotFound", "rota do site desconhecida")

    def graph(self, method: str, g: str, q: dict[str, str], req: Request, body: bytes) -> Response:
        if g.startswith("sites/"):
            return self.site(g)
        if not g.startswith("drives/"):
            return gerr(404, "itemNotFound", "rota desconhecida")
        parsed = self.parse_drive_path(g[7:])
        if parsed is None:
            return gerr(404, *NOT_FOUND)
        drive, path, suffix = parsed
        if drive not in self.drives:
            return gerr(404, "itemNotFound", "drive não encontrado")
        m = self.drives[drive]
        key = (method, suffix)
        if key == ("GET", ""):
            n = m.get(path)
            return JSONResponse(self.item_json(drive, path, n)) if n else gerr(404, *NOT_FOUND)
        if key == ("DELETE", ""):
            return self.delete_item(m, path)
        if key == ("PATCH", ""):
            return self.patch_item(drive, path, body)
        if key == ("GET", "children"):
            return self.children(drive, path, q, req)
        if key == ("POST", "children"):
            return self.create_folder(drive, path, body)
        if key == ("GET", "content"):
            n = m.get(path)
            if n is None or n["folder"]:
                return gerr(404, *NOT_FOUND)
            tok = secrets.token_hex(16)
            self.downloads[tok] = (drive, path)
            return Response(status_code=302, headers={"location": f"{base_url(req)}/_dl/{tok}"})
        if key == ("PUT", "content"):
            if len(body) > SIMPLE_MAX:
                return gerr(413, "requestTooLarge", "Use an upload session for files larger than 4 MB.")
            self.stats["simple_uploads"] += 1
            existed = path in m
            r = self.put_file(drive, path, body, req.headers.get("content-type", ""))
            if isinstance(r, Response):
                return r
            return JSONResponse(r, status_code=200 if existed else 201)
        if key == ("POST", "createUploadSession"):
            if not path:
                return gerr(400, "invalidRequest", "caminho vazio")
            sid = secrets.token_hex(16)
            self.sessions[sid] = {"drive": drive, "path": path, "total": 0, "data": bytearray()}
            self.stats["sessions"] += 1
            return JSONResponse(
                {
                    "uploadUrl": f"{base_url(req)}/_upload/{sid}",
                    "expirationDateTime": rfc3339(time.time() + 3600),
                    "nextExpectedRanges": ["0-"],
                }
            )
        if key == ("POST", "createLink"):
            return self.create_link(drive, path, body)
        if method == "DELETE" and suffix.startswith("permissions/"):
            if self.links.pop(unquote(suffix[12:]), None) is None:
                return gerr(404, "itemNotFound", "permissão não encontrada")
            return Response(status_code=204)
        return gerr(405, "invalidRequest", "método não suportado")

    def delete_item(self, m: dict[str, dict[str, Any]], path: str) -> Response:
        if not path:
            return gerr(403, "accessDenied", "não é possível apagar a raiz")
        if path not in m:
            return gerr(404, *NOT_FOUND)
        for k in [k for k in m if k == path or k.startswith(path + "/")]:
            self.bytes = max(0, self.bytes - len(m.pop(k)["data"]))
        return Response(status_code=204)

    def put_file(self, drive: str, path: str, data: bytes, ctype: str) -> dict[str, Any] | Response:
        if not path:
            return gerr(400, "invalidRequest", "caminho vazio")
        m = self.drives[drive]
        old = m.get(path)
        if old and old["folder"]:
            return gerr(409, "nameAlreadyExists", "existe uma pasta com esse nome")
        freed = len(old["data"]) if old else 0
        if self.bytes - freed + len(data) > self.cfg.max_bytes:
            return gerr(507, "quotaLimitReached", "Insufficient Storage")
        self.bytes += len(data) - freed
        cur = ""
        for s in path.split("/")[:-1]:
            cur = f"{cur}/{s}" if cur else s
            if cur not in m:
                m[cur] = self._node(self._next_id(), s, True)
        node = self._node(
            old["id"] if old else self._next_id(),
            path.rsplit("/", 1)[-1],
            False,
            bytes(data),
            ctype or "application/octet-stream",
            old["version"] + 1 if old else 1,
        )
        m[path] = node
        return self.item_json(drive, path, node)

    def download_url(self, tok: str, req: Request) -> Response:
        self.stats["downloads"] += 1
        if "authorization" in req.headers:
            self.stats["downloads_with_auth"] += 1
        loc = self.downloads.get(tok)
        if loc is None:
            return gerr(404, "itemNotFound", "link de download vencido")
        n = self.drives.get(loc[0], {}).get(loc[1])
        if n is None:
            return gerr(404, *NOT_FOUND)
        return Response(bytes(n["data"]), media_type=n["ctype"] or "application/octet-stream")

    def children(self, drive: str, path: str, q: dict[str, str], req: Request) -> Response:
        m = self.drives[drive]
        if not (m.get(path) or {}).get("folder"):
            return gerr(404, *NOT_FOUND)
        prefix = f"{path}/" if path else ""
        allv = [
            self.item_json(drive, k, n)
            for k, n in m.items()
            if k and k.startswith(prefix) and "/" not in k[len(prefix) :]
        ]
        top = min(max(int(q["$top"]) if q.get("$top", "").isdigit() else 200, 1), 999)
        skip = int(q["$skiptoken"]) if q.get("$skiptoken", "").isdigit() else 0
        out: dict[str, Any] = {"value": allv[skip : skip + top]}
        if skip + top < len(allv):
            base = base_url(req)
            loc = (
                f"{base}/v1.0/drives/{drive}/root:/{quote(path, safe='/')}:/children"
                if path
                else f"{base}/v1.0/drives/{drive}/root/children"
            )
            out["@odata.nextLink"] = f"{loc}?$top={top}&$skiptoken={skip + top}"
        return JSONResponse(out)

    def create_folder(self, drive: str, parent: str, body: bytes) -> Response:
        try:
            v = json.loads(body)
            name = str(v.get("name", ""))
        except (ValueError, AttributeError):
            return gerr(400, "invalidRequest", "corpo inválido")
        if not name or "/" in name or "folder" not in v:
            return gerr(400, "invalidRequest", "nome de pasta inválido")
        m = self.drives[drive]
        if not (m.get(parent) or {}).get("folder"):
            return gerr(404, "itemNotFound", "pasta-pai inexistente")
        full = f"{parent}/{name}" if parent else name
        if full in m:
            return gerr(
                409, "nameAlreadyExists", "An item with the same name already exists under the parent"
            )
        n = m[full] = self._node(self._next_id(), name, True)
        return JSONResponse(self.item_json(drive, full, n), status_code=201)

    def patch_item(self, drive: str, path: str, body: bytes) -> Response:
        try:
            v = json.loads(body)
        except ValueError:
            return gerr(400, "invalidRequest", "corpo inválido")
        if not isinstance(v, dict):
            return gerr(400, "invalidRequest", "corpo inválido")
        m = self.drives[drive]
        node = m.get(path)
        if node is None:
            return gerr(404, *NOT_FOUND)
        pid = (v.get("parentReference") or {}).get("id")
        if pid:
            parent = next((p for p, n in m.items() if n["id"] == pid and n["folder"]), None)
            if parent is None:
                return gerr(404, "itemNotFound", "pasta de destino inexistente")
        else:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
        name = v.get("name") or node["name"]
        dest = f"{parent}/{name}" if parent else name
        if dest != path and dest in m:
            return gerr(
                409, "nameAlreadyExists", "An item with the same name already exists under the parent"
            )
        if dest.startswith(path + "/"):
            return gerr(400, "invalidRequest", "não é possível mover para dentro de si")
        for k in [k for k in m if k == path or k.startswith(path + "/")]:
            n = m.pop(k)
            if k == path:
                n.update(name=name, modified=rfc3339(), version=n["version"] + 1)
            m[dest + k[len(path) :]] = n
        return JSONResponse(self.item_json(drive, dest, m[dest]))

    def upload_session(self, method: str, sid: str, req: Request, body: bytes) -> Response:
        if "authorization" in req.headers:
            return gerr(401, "unauthenticated", "Do not send Authorization to the upload URL.")
        s = self.sessions.get(sid)
        if s is None:
            return gerr(404, "itemNotFound", "sessão de upload inexistente ou vencida")
        if method == "DELETE":
            del self.sessions[sid]
            return Response(status_code=204)
        if method == "GET":
            return JSONResponse({"nextExpectedRanges": [f"{len(s['data'])}-"]})
        if method != "PUT":
            return gerr(405, "invalidRequest", "método")
        mt = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", req.headers.get("content-range", ""))
        if not mt:
            return gerr(400, "invalidRange", "Content-Range inválido")
        a, b, total = (int(x) for x in mt.groups())
        if b < a or b - a + 1 != len(body) or b >= total or (s["total"] and s["total"] != total):
            return gerr(400, "invalidRange", "Content-Range não confere com o corpo")
        if b + 1 != total and (b - a + 1) % CHUNK_UNIT:
            return gerr(400, "invalidRange", "pedaço precisa ser múltiplo de 320 KiB")
        have = len(s["data"])
        if a > have:
            return gerr(416, "invalidRange", "fora de ordem")
        if b < have:
            return JSONResponse({"nextExpectedRanges": [f"{have}-"]}, status_code=202)
        s["data"].extend(body[have - a :])
        s["total"] = total
        self.stats["chunks"] += 1
        if len(s["data"]) == total:
            del self.sessions[sid]
            r = self.put_file(s["drive"], s["path"], bytes(s["data"]), "application/octet-stream")
            return r if isinstance(r, Response) else JSONResponse(r, status_code=201)
        return JSONResponse({"nextExpectedRanges": [f"{len(s['data'])}-"]}, status_code=202)

    def create_link(self, drive: str, path: str, body: bytes) -> Response:
        try:
            v = json.loads(body)
            kind, scope = v.get("type", ""), v.get("scope", "anonymous")
        except (ValueError, AttributeError):
            return gerr(400, "invalidRequest", "corpo inválido")
        if kind not in ("view", "edit", "embed") or scope not in ("anonymous", "organization", "users"):
            return gerr(400, "invalidRequest", "type/scope inválidos")
        n = self.drives[drive].get(path)
        if n is None:
            return gerr(404, *NOT_FOUND)
        code = secrets.token_urlsafe(16)[:22]
        c = self.cfg
        out: dict[str, Any] = {
            "id": f"lnk-{code}",
            "roles": ["write" if kind == "edit" else "read"],
            "link": {
                "type": kind,
                "scope": scope,
                "webUrl": f"https://{c.hostname}/:{'f' if n['folder'] else 'b'}:/s/{c.site_name}/{n['id']}?e={code}",
            },
        }
        if c.link_expiry == "extend":
            out["expirationDateTime"] = rfc3339(time.time() + 30 * 86400)
        elif c.link_expiry != "ignore" and v.get("expirationDateTime"):
            out["expirationDateTime"] = v["expirationDateTime"]
        self.links[f"lnk-{code}"] = path
        return JSONResponse(out, status_code=201)


def make_app(sim: Sim | None = None) -> Starlette:
    sim = sim or Sim(SimConfig.from_env())
    app = Starlette(
        routes=[Route("/{p:path}", sim.handle, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])]
    )
    app.state.sim = sim
    return app
