"""`hub mcp`: stdio <-> Streamable HTTP bridge to the hub's MCP (one JSON-RPC message per line)."""

import base64
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any, TextIO, cast
from urllib.parse import urlparse

import httpx

MSG_SEM_CREDENCIAL = (
    "defina o token (HUB_TOKEN, --token-file ou --token): crie em Acesso › Tokens de API "
    "ou em POST /api/me/tokens, com o escopo mcp"
)
WARN = (
    "aviso: --token fica visível na lista de processos e no histórico do shell; "
    "prefira HUB_TOKEN ou --token-file"
)
DISABLED = (
    "O servidor MCP está desligado neste hub ([mcp].enabled = false no arquivo de configuração, "
    "ou HUB_MCP_ENABLED=0). Peça à administração da plataforma para ligá-lo."
)


def rpc_error(id_: Any, code: int, msg: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


def check_hub_url(hub: str) -> None:
    """The token is only sent over HTTPS, or over HTTP to the local machine itself."""
    u = urlparse(hub)
    if u.scheme not in ("http", "https"):
        raise ValueError(f"esquema {u.scheme or hub}:// não suportado (use https://)")
    h = u.hostname
    if not h:
        raise ValueError(f"URL do hub inválida: {hub}")
    if u.scheme == "https" or h.lower() == "localhost":
        return
    try:
        if ipaddress.ip_address(h).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(
        f"recusado: {hub} usa http:// fora da máquina local — o token iria em texto claro. Use https://"
    )


def resolve_token(cli: str | None, file: Path | None, env: str | None) -> tuple[str, str | None]:
    """File > HUB_TOKEN > --token (with a warning)."""
    if file is not None:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            raise ValueError(f"lendo o token de {file}: {e}") from e
        t = (lines[0] if lines else "").strip()
        if not t:
            raise ValueError(f"arquivo de token vazio: {file}")
        return t, None
    if env and env.strip():
        return env.strip(), None
    if cli and cli.strip():
        return cli.strip(), WARN
    raise ValueError(MSG_SEM_CREDENCIAL)


def sse_messages(body: str) -> list[Any]:
    out: list[Any] = []
    for ev in body.replace("\r\n", "\n").split("\n\n"):
        data = [ln[5:].removeprefix(" ") for ln in ev.split("\n") if ln.startswith("data:")]
        if not data:
            continue
        try:
            out.append(json.loads("\n".join(data)))
        except ValueError:
            continue
    return out


def header_safe(v: str) -> str:
    plain = (
        bool(v)
        and all(0x20 <= ord(c) < 0x7F for c in v)
        and v.strip() == v
        and not (v.startswith("=?base64?") and v.endswith("?="))
    )
    return v if plain else "=?base64?" + base64.b64encode(v.encode()).decode() + "?="


class Bridge:
    def __init__(self, hub: str, token: str, path: str = "/mcp", client: httpx.Client | None = None):
        check_hub_url(hub)
        self.url = hub.rstrip("/") + (path if path.startswith("/") else "/" + path)
        self.token = token
        self.session: str | None = None
        self.protocol: str | None = None
        self.http = client or httpx.Client(timeout=httpx.Timeout(300, connect=10))

    def _headers(self, method: Any, params: dict[str, Any]) -> dict[str, str]:
        h = {"authorization": f"Bearer {self.token}", "accept": "application/json, text/event-stream"}
        if self.session:
            h["mcp-session-id"] = self.session
        meta = params.get("_meta")
        ver = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
        if isinstance(ver, str):
            h["mcp-protocol-version"] = ver
            if isinstance(method, str):
                h["mcp-method"] = method
                key = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}.get(method)
                if key and isinstance(params.get(key), str):
                    h["mcp-name"] = header_safe(params[key])
        elif self.protocol:
            h["mcp-protocol-version"] = self.protocol
        return h

    def forward(self, line: str) -> tuple[str, list[Any]]:
        """Returns (`reply`|`accepted`|`disabled`, messages to write)."""
        try:
            msg = json.loads(line)
        except ValueError as e:
            return "reply", [rpc_error(None, -32700, f"JSON inválido: {e}")]
        if not isinstance(msg, dict):
            return "reply", [rpc_error(None, -32600, "mensagem JSON-RPC inválida")]
        params = msg.get("params")
        headers = self._headers(msg.get("method"), params if isinstance(params, dict) else {})
        try:
            res = self.http.post(self.url, json=msg, headers=headers)
        except httpx.HTTPError as e:
            return "reply", [rpc_error(msg.get("id"), -32603, f"hub inacessível em {self.url}: {e}")]
        return self._reply(res, msg)

    def _reply(self, res: httpx.Response, msg: dict[str, Any]) -> tuple[str, list[Any]]:
        id_ = msg.get("id")
        is_init = msg.get("method") == "initialize"
        st = res.status_code
        if st == 404 and (is_init or self.session is None):
            return "disabled", [rpc_error(id_, -32601, DISABLED)]
        if st == 404:
            self.session = None  # session expired: the client initializes again
        if st == 202:
            return "accepted", []
        if sid := res.headers.get("mcp-session-id"):
            self.session = sid
        if res.headers.get("content-type", "").startswith("text/event-stream"):
            msgs = sse_messages(res.text)
            return "reply", msgs or [rpc_error(id_, -32603, "hub encerrou o stream sem resposta")]
        try:
            v = res.json()
        except ValueError:
            if "id" not in msg:
                return "accepted", []
            return "reply", [rpc_error(id_, -32603, f"hub respondeu {st}")]
        return self._json_reply(v, id_, st, is_init=is_init)

    def _json_reply(self, v: Any, id_: Any, st: int, *, is_init: bool) -> tuple[str, list[Any]]:
        if isinstance(v, dict) and "jsonrpc" in v:
            r = v.get("result")
            if is_init and isinstance(r, dict) and isinstance(r.get("protocolVersion"), str):
                self.protocol = r["protocolVersion"]
            return "reply", [v]
        d = v if isinstance(v, dict) else {}
        err = d.get("error") if isinstance(d.get("error"), str) else d.get("detail")
        return "reply", [
            rpc_error(id_, -32603, f"hub respondeu {st}: {err if isinstance(err, str) else 'erro'}")
        ]


def run(
    hub: str,
    token: str,
    path: str,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    client: httpx.Client | None = None,
) -> int:
    if not token:
        raise ValueError(MSG_SEM_CREDENCIAL)
    src: TextIO = stdin or cast(TextIO, sys.stdin)
    dst: TextIO = stdout or cast(TextIO, sys.stdout)
    bridge = Bridge(hub, token, path, client)
    print(f"hub mcp: ponte stdio → {bridge.url}", file=sys.stderr)
    for line in src:
        if not line.strip():
            continue
        kind, msgs = bridge.forward(line)
        for m in msgs:
            dst.write(json.dumps(m, ensure_ascii=False) + "\n")
        dst.flush()
        if kind == "disabled":
            print("hub mcp: o servidor MCP está desligado neste hub ([mcp].enabled = false)", file=sys.stderr)
            return 3
    return 0
