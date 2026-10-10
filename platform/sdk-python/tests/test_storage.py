"""hub_sdk.storage (onda 10): confinamento de caminhos (vetores compartilhados com o Rust),
URL pré-assinada S3 idêntica à do Rust e o cliente do broker contra um broker falso."""

from __future__ import annotations

import datetime as dt
import io
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
from typing_extensions import override

from hub_sdk import storage

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "vectors" / "storage-vectors.json").read_text("utf-8")
)
TOKEN = "token-do-broker-0123456789"


@pytest.mark.parametrize(("raw", "clean"), VECTORS["paths_ok"])
def test_paths_ok(raw: str, clean: str) -> None:
    assert storage.clean_path(raw, file=False) == clean


@pytest.mark.parametrize("raw", VECTORS["paths_bad"])
def test_paths_bad(raw: str) -> None:
    with pytest.raises(storage.StorageError) as e:
        storage.clean_path(raw, file=False)
    assert e.value.status == 400


def test_presign_matches_rust_vector() -> None:
    p = VECTORS["presign"]
    c = storage.S3Connection(
        "s3",
        storage.S3Config(
            endpoint=p["endpoint"],
            bucket=p["bucket"],
            access_key=p["access_key"],
            secret_key=p["secret_key"],
            region=p["region"],
            public_url=p["public_url"],
        ),
    )
    now = dt.datetime.fromtimestamp(p["now"], dt.UTC)
    assert c.presign_get(p["key"], p["expires"], now) == p["url"]
    assert c.presign("PUT", p["key"], p["expires"], now) == p["put_url"]
    assert p["secret_key"] not in repr(c)


class FakeBroker(BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}
    seen: ClassVar[list[tuple[str, str, str]]] = []
    fail_next: ClassVar[list[int]] = []

    @override
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _reply(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method: str) -> None:
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self.headers.get("authorization") != f"Bearer {TOKEN}":
            return self._reply(401, {"error": "token"})
        self.seen.append(
            (
                method,
                u.path,
                self.headers.get("x-hub-user", "") + "|" + self.headers.get("x-hub-user-sig", ""),
            )
        )
        if self.fail_next:
            code = self.fail_next.pop(0)
            return self._reply(code, {"error": "limitado"}, {"retry-after": "1"})
        base = "/_hub/storage/docs/"
        if not u.path.startswith(base):
            return self._reply(404, {"error": "conexão"})
        op = u.path[len(base) :]
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        if op == "content" and method == "PUT":
            self.files[q["path"]] = body
            name = q["path"].rsplit("/", 1)[-1]
            return self._reply(201, {"path": q["path"], "name": name, "size": len(body), "folder": False})
        if op == "content" and method == "GET":
            if q["path"] not in self.files:
                return self._reply(404, {"error": "não encontrado"})
            return self._reply(200, self.files[q["path"]], {"content-type": "application/pdf"})
        if op == "content" and method == "DELETE":
            self.files.pop(q["path"], None)
            return self._reply(204, b"")
        if op == "list":
            pre = q.get("prefix", "")
            items = [
                {"path": k, "name": k.rsplit("/", 1)[-1], "size": len(v), "folder": False}
                for k, v in self.files.items()
                if k.startswith(f"{pre}/" if pre else "")
            ]
            return self._reply(200, {"items": items})
        if op == "move":
            m = json.loads(body)
            self.files[m["to"]] = self.files.pop(m["from"])
            return self._reply(200, {"path": m["to"], "name": m["to"].rsplit("/", 1)[-1], "size": 1})
        if op == "share-link":
            return self._reply(200, {"url": "https://contoso.sharepoint.com/:b:/x", "kind": "view"})
        return self._reply(404, {"error": "rota"})

    def do_GET(self) -> None:
        self._route("GET")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def do_POST(self) -> None:
        self._route("POST")


@pytest.fixture
def broker() -> Iterator[str]:
    FakeBroker.files.clear()
    FakeBroker.seen.clear()
    FakeBroker.fail_next.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBroker)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_broker_connection_roundtrip(broker: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_CONNECTIONS", "docs=sharepoint,outra=s3")
    monkeypatch.setenv("HUB_STORAGE_URL", broker)
    monkeypatch.setenv("HUB_STORAGE_TOKEN", TOKEN)
    assert storage.bound() == [("docs", "sharepoint"), ("outra", "s3")]
    assert storage.connection("nao-vinculada") is None
    c = storage.connection("docs")
    assert c is not None and c.via_broker and c.kind == "sharepoint"
    c = c.for_user("dXNlcg", "v1.1.abc")
    data = b"%PDF-1.4 " + bytes(range(256)) * 400
    it = await c.upload("CT-1/contrato.pdf", io.BytesIO(data), "application/pdf")
    assert (it.path, it.size) == ("CT-1/contrato.pdf", len(data))
    await c.upload("CT-1/b.txt", b"ola", "text/plain")
    d = await c.download("CT-1/contrato.pdf")
    assert d.content_type == "application/pdf" and d.length == len(data)
    assert b"".join([ch async for ch in d.chunks()]) == data
    names = sorted(i.path for i in await c.list("CT-1"))
    assert names == ["CT-1/b.txt", "CT-1/contrato.pdf"]
    moved = await c.move("CT-1/b.txt", "CT-2/b.txt")
    assert moved.path == "CT-2/b.txt"
    link = await c.share_link("CT-2/b.txt", "view", 2)
    assert link.url.startswith("https://contoso.sharepoint.com/")
    await c.delete("CT-2/b.txt")
    with pytest.raises(storage.StorageError) as e:
        await c.download("CT-2/b.txt")
    assert e.value.status == 404 and not e.value.transient
    assert all(user == "dXNlcg|v1.1.abc" for _, _, user in FakeBroker.seen), (
        "x-hub-user e assinatura repassados"
    )
    assert c.for_user(None, "v1.1.abc").user_sig is None, "assinatura sem usuário não vai"
    # 503 com Retry-After curto: repetido sozinho (operações repetíveis).
    FakeBroker.fail_next.append(503)
    assert len(await c.list("CT-1")) == 1
    # Caminho que escapa: nunca sai do app.
    before = len(FakeBroker.seen)
    for bad in ["../x", "a/../../b", "%2e%2e/x"]:
        with pytest.raises(storage.StorageError):
            await c.download(bad)
    assert len(FakeBroker.seen) == before


async def test_wrong_token_and_unreachable_broker() -> None:
    c = storage.BrokerConnection("docs", "sharepoint", "http://127.0.0.1:9", TOKEN)
    with pytest.raises(storage.StorageError) as e:
        await c.meta("a.pdf")
    assert e.value.status == 503 and e.value.transient


async def test_wrong_token_is_401(broker: str) -> None:
    c = storage.BrokerConnection("docs", "sharepoint", broker, "outro-token-000000000000")
    with pytest.raises(storage.StorageError) as e:
        await c.list("")
    assert e.value.status == 401
