"""`hub mcp`: stdio <-> Streamable HTTP bridge to the hub's MCP (one JSON-RPC message per line)."""

import base64
import json
import sys
from typing import Any, TextIO, cast

import httpx

from hub_cli import net

from .auth import Session, check_hub_url
from .errors import CliError

__all__ = ["Bridge", "check_hub_url", "run"]

DISABLED = (
    "O servidor MCP está desligado neste hub ([mcp].enabled = false no arquivo de configuração, "
    "ou HUB_MCP_ENABLED=0). Peça à administração da plataforma para ligá-lo."
)


def rpc_error(id_: Any, code: int, msg: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


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
    def __init__(self, session: Session, path: str = "/mcp", client: httpx.Client | None = None):
        check_hub_url(session.hub)
        self.url = session.hub.rstrip("/") + (path if path.startswith("/") else "/" + path)
        self.auth = session
        self.session: str | None = None
        self.protocol: str | None = None
        self.http = client or net.client(timeout=httpx.Timeout(300, connect=10))

    def _headers(self, method: Any, params: dict[str, Any]) -> dict[str, str]:
        h = {"accept": "application/json, text/event-stream"}
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
            res = self.auth.request(self.http, "POST", self.url, json=msg, headers=headers)
        except httpx.HTTPError as e:
            return "reply", [rpc_error(msg.get("id"), -32603, f"hub inacessível em {self.url}: {net.why(e)}")]
        except CliError as e:  # the CLI session could not be refreshed: the client sees why
            return "reply", [rpc_error(msg.get("id"), -32603, str(e))]
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
    session: Session,
    path: str,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    client: httpx.Client | None = None,
) -> int:
    src: TextIO = stdin or cast(TextIO, sys.stdin)
    dst: TextIO = stdout or cast(TextIO, sys.stdout)
    bridge = Bridge(session, path, client)
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
