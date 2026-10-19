"""Microsoft Graph (SharePoint) client of the broker: app-only with the client secret, no SDK."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from hub.httpclients import external_client
from hub.storage import (
    Body,
    Item,
    StorageError,
    clean,
    clean_file,
    encode,
    join,
    parse_rfc3339,
    redact,
    rfc3339,
    split_parent,
)
from hub.storage.cert import ClientCert
from hub.storage.graph_probe import probe

DEFAULT_LOGIN = "https://login.microsoftonline.com"
DEFAULT_GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"
SIMPLE_MAX = 4 * 1024 * 1024
CHUNK = 16 * 320 * 1024


@dataclass
class Endpoints:
    login: str = DEFAULT_LOGIN
    graph: str = DEFAULT_GRAPH

    @classmethod
    def from_env(cls) -> Endpoints:
        v = lambda k, d: (os.getenv(k) or d).strip().rstrip("/")  # noqa: E731
        return cls(v("HUB_GRAPH_LOGIN_URL", DEFAULT_LOGIN), v("HUB_GRAPH_URL", DEFAULT_GRAPH))

    def is_default(self) -> bool:
        return self.login == DEFAULT_LOGIN and self.graph == DEFAULT_GRAPH

    def content_url_ok(self, url: str, site: str) -> bool:
        """Pre-authenticated download/upload only on Graph, SharePoint or the configured simulator hosts."""
        u = urlsplit(url)
        host = u.hostname or ""
        if not self.is_default():
            return u.netloc == urlsplit(self.graph).netloc
        sp = urlsplit(site).hostname if site.startswith("https://") else None
        return u.scheme == "https" and (
            host == urlsplit(self.graph).hostname or host.endswith(".sharepoint.com") or host == sp
        )


def valid_tenant(t: str) -> bool:
    return bool(t) and len(t) <= 100 and re.fullmatch(r"[A-Za-z0-9.\-]+", t) is not None


def valid_guid(t: str) -> bool:
    return re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", t) is not None


def valid_site(s: str) -> bool:
    if s.startswith("https://"):
        rest = s[8:]
        host = rest.split("/")[0]
        return (
            bool(host)
            and re.fullmatch(r"[A-Za-z0-9.\-]+", host) is not None
            and not re.search(r"[?#\\ ]", rest)
        )
    return bool(s) and len(s) <= 300 and re.fullmatch(r"[A-Za-z0-9.,\-]+", s) is not None


@dataclass
class SharePointConfig:
    tenant_id: str
    client_id: str
    secret: str = field(repr=False)
    site: str
    drive: str = ""
    base_folder: str = ""
    cert: ClientCert | None = field(default=None, repr=False)


class GraphClient:
    def __init__(
        self, ep: Endpoints, cfg: SharePointConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.ep, self.cfg = ep, cfg
        self.http = external_client(transport=transport, timeout=60.0, follow_redirects=False)
        self.chunk, self.simple_max = CHUNK, SIMPLE_MAX
        self._token: tuple[str, float] | None = None
        self._target: tuple[str, str, str] | None = None
        self.token_fetches = 0

    async def aclose(self) -> None:
        await self.http.aclose()

    # ---- token e chamadas ------------------------------------------------------------------
    async def _fetch_token(self) -> tuple[str, float]:
        self.token_fetches += 1
        url = f"{self.ep.login}/{self.cfg.tenant_id}/oauth2/v2.0/token"
        form = {
            "client_id": self.cfg.client_id,
            "scope": SCOPE,
            "grant_type": "client_credentials",
        }
        if self.cfg.cert is not None:
            form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            form["client_assertion"] = self.cfg.cert.assertion(self.cfg.client_id, url)
        else:
            form["client_secret"] = self.cfg.secret
        try:
            r = await self.http.post(url, data=form, timeout=20)
        except httpx.HTTPError as e:
            raise StorageError("unavailable", "login no Entra ID: provedor inacessível", 2) from e
        body = _json(r)
        if r.is_success and body.get("access_token"):
            return str(body["access_token"]), time.monotonic() + max(
                60, int(body.get("expires_in", 3600)) - 120
            )
        raise self._token_error(r.status_code, body)

    def _token_error(self, status: int, body: dict[str, Any]) -> StorageError:
        code, desc = str(body.get("error", "")), str(body.get("error_description", ""))
        aadsts = next((w.rstrip(":") for w in desc.split() if w.startswith("AADSTS")), "")
        if aadsts == "AADSTS90002" or ("Tenant" in desc and status == 400 and code == "invalid_request"):
            kind, msg = "invalid", "Tenant não encontrado (confira o Tenant ID)."
        elif aadsts == "AADSTS700016" or code == "unauthorized_client":
            kind, msg = "auth", "Client ID não encontrado neste tenant (app registration)."
        elif aadsts == "AADSTS7000222":
            kind, msg = (
                "auth",
                'O segredo do cliente expirou: gere outro no Entra ID e use "Rotacionar credencial".',
            )
        elif code == "invalid_client":
            kind, msg = "auth", "Credencial recusada pelo Entra ID (segredo ou certificado inválido)."
        else:
            kind, msg = "upstream", "O Entra ID recusou o pedido de token."
        detail = f"{msg} ({aadsts})" if aadsts else f"{msg} (HTTP {status} {code})"
        return StorageError(kind, redact(detail, [self.cfg.secret]))

    async def token(self) -> str:
        if self._token is None or time.monotonic() >= self._token[1]:
            self._token = await self._fetch_token()
        return self._token[0]

    async def _call(self, what: str, method: str, url: str, **kw: Any) -> httpx.Response:
        for rnd in range(2):
            h = {"Authorization": f"Bearer {await self.token()}"} | kw.pop("headers", {})
            for attempt in range(3):
                try:
                    r = await self.http.request(method, url, headers=h, **kw)
                except httpx.HTTPError as e:
                    raise StorageError("unavailable", f"{what}: provedor inacessível", 2) from e
                if r.status_code not in (429, 503, 504) or attempt == 2:
                    break
                await asyncio.sleep(min(float(r.headers.get("retry-after", "0") or 0), 2.0))
            if r.status_code == 401 and rnd == 0:
                self._token = None
                continue
            return r
        raise AssertionError  # pragma: no cover

    def _fail(self, what: str, r: httpx.Response) -> StorageError:
        n = r.status_code
        ra = r.headers.get("retry-after", "")
        gmsg = (
            str(_json(r).get("error", {}).get("message", ""))[:200]
            if isinstance(_json(r).get("error"), dict)
            else ""
        )
        table = {
            400: ("invalid", f"{what}: pedido recusado pelo Graph"),
            401: ("auth", f"{what}: token recusado pelo Graph"),
            403: (
                "forbidden",
                f"{what}: sem permissão no site (com Sites.Selected, a TI precisa conceder acesso a este site ao app)",
            ),
            404: ("not_found", f"{what}: não encontrado"),
            409: ("conflict", f"{what}: já existe ou foi alterado"),
            412: ("conflict", f"{what}: já existe ou foi alterado"),
            413: ("too_large", f"{what}: arquivo grande demais para o provedor"),
        }
        if n in (429, 502, 503, 504):
            return StorageError(
                "unavailable",
                f"{what}: provedor limitando ou indisponível (HTTP {n})",
                int(ra) if ra.isdigit() else None,
            )
        kind, msg = table.get(n, ("upstream", f"{what}: HTTP {n} do Graph"))
        return StorageError(kind, msg, detail=redact(gmsg, [self.cfg.secret]))

    async def _json(self, what: str, r: httpx.Response) -> dict[str, Any]:
        if not r.is_success:
            raise self._fail(what, r)
        body = _json(r)
        if not body and r.content:
            raise StorageError("upstream", f"{what}: resposta ilegível")
        return body

    # ---- site, biblioteca e itens ----------------------------------------------------------
    async def _resolve_site(self) -> tuple[str, str]:
        g, site = self.ep.graph, self.cfg.site
        if site.startswith("https://"):
            host, _, p = site[8:].partition("/")
            p = p.strip("/")
            url = f"{g}/sites/{host}:/{encode(p)}" if p else f"{g}/sites/{host}"
        else:
            url = f"{g}/sites/{encode(site)}"
        v = await self._json("Site", await self._call("site", "GET", url))
        if not v.get("id"):
            raise StorageError("upstream", "Site sem id na resposta do Graph.")
        return str(v["id"]), str(v.get("displayName") or v.get("name") or "")

    async def _resolve_drive(self, site_id: str) -> tuple[str, str]:
        g, sid = self.ep.graph, encode(site_id)
        if not self.cfg.drive.strip():
            v = await self._json(
                "Biblioteca padrão", await self._call("biblioteca", "GET", f"{g}/sites/{sid}/drive")
            )
            return str(v.get("id", "")), str(v.get("name", ""))
        v = await self._json(
            "Bibliotecas do site", await self._call("bibliotecas", "GET", f"{g}/sites/{sid}/drives")
        )
        want = self.cfg.drive.strip()
        for d in v.get("value", []):
            if d.get("id") == want or str(d.get("name", "")).lower() == want.lower():
                return str(d["id"]), str(d.get("name", ""))
        raise StorageError("not_found", f'Biblioteca "{want}" não encontrada no site.')

    async def _drive(self) -> str:
        if self._target is None:
            sid, site_name = await self._resolve_site()
            drive_id, drive_name = await self._resolve_drive(sid)
            self._target = (drive_id, site_name, drive_name)
        return self._target[0]

    def _item_url(self, drive: str, full: str, suffix: str = "") -> str:
        g, d = self.ep.graph, encode(drive)
        if not full:
            return f"{g}/drives/{d}/root" + (f"/{suffix}" if suffix else "")
        return f"{g}/drives/{d}/root:/{encode(full)}" + (f":/{suffix}" if suffix else "")

    def _full(self, rel: str) -> str:
        return join(self.cfg.base_folder, rel)

    @staticmethod
    def _item(v: dict[str, Any], rel: str) -> Item:
        return Item(
            path=rel,
            name=str(v.get("name", "")),
            size=int(v.get("size") or 0),
            modified=str(v.get("lastModifiedDateTime", "")),
            folder=bool(v.get("folder")),
            content_type=(v.get("file") or {}).get("mimeType"),
            etag=v.get("eTag"),
        )

    async def _item_json(self, full: str) -> dict[str, Any]:
        url = self._item_url(await self._drive(), full)
        return await self._json("Item", await self._call("item", "GET", url))

    async def meta(self, rel: str) -> Item:
        rel = clean(rel)
        return self._item(await self._item_json(self._full(rel)), rel)

    async def ls(self, prefix: str = "") -> list[Item]:
        prefix = clean(prefix)
        url: str | None = self._item_url(await self._drive(), self._full(prefix), "children") + "?$top=200"
        out: list[Item] = []
        while url:
            v = await self._json("Listagem", await self._call("listagem", "GET", url))
            out += [self._item(it, join(prefix, str(it.get("name", "")))) for it in v.get("value", [])]
            nxt = v.get("@odata.nextLink")
            # Only follows links from Graph itself (no SSRF through a forged response).
            url = (
                nxt
                if isinstance(nxt, str) and nxt.startswith(self.ep.graph + "/") and len(out) < 5000
                else None
            )
        return out

    async def download(self, rel: str) -> tuple[str, int | None, AsyncIterator[bytes]]:
        rel = clean_file(rel)
        url = self._item_url(await self._drive(), self._full(rel), "content")
        r = await self._call("download", "GET", url)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            if not self.ep.content_url_ok(loc, self.cfg.site):
                raise StorageError("upstream", "Download: redirecionamento inválido do Graph.")
            r = await self.http.get(loc)  # pre-authenticated URL: without the hub's token
        if not r.is_success:
            raise self._fail("Download", r)
        n = r.headers.get("content-length", "")

        async def body() -> AsyncIterator[bytes]:
            yield r.content

        return (
            r.headers.get("content-type", "application/octet-stream"),
            int(n) if n.isdigit() else None,
            body(),
        )

    async def upload(self, rel: str, length: int, content_type: str, body: Body) -> Item:
        rel = clean_file(rel)
        drive = await self._drive()
        full = self._full(rel)
        if length <= self.simple_max:
            data = await body.read(length)
            await body.expect_end()
            url = self._item_url(drive, full, "content") + "?@microsoft.graph.conflictBehavior=replace"
            r = await self._call("upload", "PUT", url, content=data, headers={"Content-Type": content_type})
            return self._item(await self._json("Upload", r), rel)
        url = self._item_url(drive, full, "createUploadSession")
        r = await self._call(
            "sessão de upload", "POST", url, json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}
        )
        up = str((await self._json("Sessão de upload", r)).get("uploadUrl", ""))
        if not self.ep.content_url_ok(up, self.cfg.site):
            raise StorageError("upstream", "Sessão de upload sem URL válida.")
        try:
            return self._item(await self._chunks(up, length, body), rel)
        except StorageError:
            await self.http.delete(up, timeout=10)  # frees the space reserved at the provider
            raise

    async def _chunks(self, up: str, length: int, body: Body) -> dict[str, Any]:
        offset = 0
        while True:
            n = min(self.chunk, length - offset)
            data = await body.read(n)
            end = offset + n - 1
            r = await self.http.put(
                up, content=data, headers={"Content-Range": f"bytes {offset}-{end}/{length}"}, timeout=120
            )
            if r.status_code in (200, 201):
                if end + 1 != length:
                    raise StorageError("upstream", "Sessão de upload terminou antes do fim do arquivo.")
                await body.expect_end()
                return _json(r)
            if r.status_code != 202:
                raise self._fail("Upload", r)
            offset = end + 1
            if offset >= length:
                raise StorageError("upstream", "Sessão de upload não confirmou o fim.")

    async def delete(self, rel: str) -> None:
        rel = clean_file(rel)
        r = await self._call("exclusão", "DELETE", self._item_url(await self._drive(), self._full(rel)))
        if not r.is_success:
            raise self._fail("Exclusão", r)

    async def _ensure_folder(self, drive: str, full: str) -> str:
        if not full:
            return str(
                (
                    await self._json(
                        "Pasta raiz", await self._call("pasta raiz", "GET", self._item_url(drive, ""))
                    )
                ).get("id", "")
            )
        r = await self._call("pasta", "GET", self._item_url(drive, full))
        if r.is_success:
            v = _json(r)
            if "folder" not in v:
                raise StorageError("conflict", f'"{full}" existe e não é pasta.')
            return str(v.get("id", ""))
        if r.status_code != 404:
            raise self._fail("Pasta", r)
        parent, name = split_parent(full)
        pid = await self._ensure_folder(drive, parent)
        url = f"{self.ep.graph}/drives/{encode(drive)}/items/{encode(pid)}/children"
        body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
        r = await self._call("criar pasta", "POST", url, json=body)
        if r.status_code == 409:
            return str((await self._item_json(full)).get("id", ""))
        return str((await self._json("Criar pasta", r)).get("id", ""))

    async def move(self, src: str, dst: str) -> Item:
        src, dst = clean_file(src), clean_file(dst)
        drive = await self._drive()
        parent, name = split_parent(self._full(dst))
        pid = await self._ensure_folder(drive, parent)
        body = {"parentReference": {"id": pid}, "name": name}
        r = await self._call("mover", "PATCH", self._item_url(drive, self._full(src)), json=body)
        return self._item(await self._json("Mover", r), dst)

    async def share_link(self, rel: str, kind: str, scope: str, expires_s: int | None) -> dict[str, Any]:
        rel = clean_file(rel)
        if kind not in ("view", "edit"):
            raise StorageError("invalid", "Tipo de link: view ou edit.")
        drive = await self._drive()
        item_id = str((await self._item_json(self._full(rel))).get("id", ""))
        url = f"{self.ep.graph}/drives/{encode(drive)}/items/{encode(item_id)}/createLink"
        expires = rfc3339(time.time() + expires_s) if expires_s else None
        body: dict[str, Any] = {"type": kind, "scope": scope}
        if expires:
            body["expirationDateTime"] = expires
        v = await self._json("Link de compartilhamento", await self._call("link", "POST", url, json=body))
        got = v.get("expirationDateTime")
        if expires_s:
            parsed = parse_rfc3339(got) if isinstance(got, str) else None
            if parsed is None or parsed > time.time() + expires_s + 300:
                perm = str(v.get("id", ""))
                removed = bool(perm) and await self._delete_permission(drive, item_id, perm)
                raise StorageError(
                    "upstream",
                    "Link de compartilhamento: o SharePoint não aplicou a validade pedida; o link foi apagado."
                    if removed
                    else "Link de compartilhamento: o SharePoint não aplicou a validade pedida e o link não pôde ser apagado. Revise as permissões do item.",
                    detail=f"validade devolvida: {got or 'nenhuma'}",
                )
        return {"url": (v.get("link") or {}).get("webUrl", ""), "kind": kind, "expires": got or expires}

    async def _delete_permission(self, drive: str, item_id: str, perm: str) -> bool:
        url = f"{self.ep.graph}/drives/{encode(drive)}/items/{encode(item_id)}/permissions/{encode(perm)}"
        try:
            return (await self._call("remover link", "DELETE", url)).is_success
        except StorageError:
            return False

    async def test(self) -> list[dict[str, Any]]:
        return await probe(self)

    def resolved(self) -> tuple[str, str] | None:
        return (self._target[1], self._target[2]) if self._target else None


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        v = r.json()
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}
