"""CLI credentials: `hub login` (OAuth 2.0 Device Authorization Grant, RFC 8628), `hub logout`, and the ONE
token resolution shared by deploy and mcp (explicit token > saved OAuth login, refreshed > legacy saved token).

The refresh token rotates on every use and reusing a dead one revokes the whole session on the hub, so the
new pair is written to disk (atomically, owner-only) before the new access token is used.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

import httpx

from hub_cli import net

from .errors import AUTH, FAIL, UNREACHABLE, VALIDATION, CliError

CLIENT_ID = "hub-cli"
SCOPE = "mcp api:read api:write app-api deploy"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_MARGIN = 60  # seconds: an access token this close to expiring is refreshed before use
MSG_NO_AUTH = "não autenticado: rode `hub login` (abre o navegador para você aprovar o acesso)"
MSG_EXPIRED = "a sessão do CLI expirou ou foi revogada: rode `hub login` de novo"
WARN = (
    "aviso: --token fica visível na lista de processos e no histórico do shell; "
    "prefira `hub login`, HUB_TOKEN ou --token-file"
)
DEVICE_FATAL = {
    "access_denied": "acesso negado no navegador: login cancelado",
    "expired_token": "o código expirou antes da aprovação: rode `hub login` de novo",
}


class Login(TypedDict, total=False):
    hub: str
    token: str  # legacy: personal token saved by `hub login --token…`
    access_token: str
    refresh_token: str
    expires_at: float


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
        f"recusado: {hub} usa http:// fora da máquina local - o token iria em texto claro. Use https://"
    )


def cred_path() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "hub-cli" / "credentials.json"


def save_login(login: Login) -> Path:
    """Atomic replace of an owner-only file (mkstemp creates it 0600): a crash never leaves half a pair."""
    p = cred_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".credentials-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(login, f)
    os.replace(tmp, p)
    return p


def load_login() -> Login:
    try:
        data = json.loads(cred_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return cast(Login, {k: v for k, v in data.items() if k in Login.__annotations__})


def explicit_token(cli: str | None, file: Path | None) -> tuple[str, str | None] | None:
    """--token-file > HUB_TOKEN > --token (with a warning); None = no explicit token."""
    if file is not None:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            raise CliError(f"lendo o token de {file}: {e}", VALIDATION) from e
        t = (lines[0] if lines else "").strip()
        if not t:
            raise CliError(f"arquivo de token vazio: {file}", VALIDATION)
        return t, None
    env = (os.environ.get("HUB_TOKEN") or "").strip()
    if env:
        return env, None
    if cli and cli.strip():
        return cli.strip(), WARN
    return None


def pick_hub(hub: str | None, saved: Login, fallback: str = "") -> str:
    h = (hub or os.environ.get("HUB_URL") or saved.get("hub") or fallback).rstrip("/")
    if not h:
        raise CliError("informe o hub: --hub URL, HUB_URL ou `hub login --hub URL`", VALIDATION)
    try:
        check_hub_url(h)
    except ValueError as e:
        raise CliError(str(e), VALIDATION) from e
    return h


def _post_form(hub: str, path: str, form: dict[str, str]) -> httpx.Response:
    try:
        with net.client(timeout=30) as c:
            return c.post(hub + path, data=form)
    except httpx.HTTPError as e:
        raise CliError(f"hub inacessível em {hub}: {net.why(e)}", UNREACHABLE) from e


def _oauth_error(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, str) else f"HTTP {r.status_code}"


def _login_from(hub: str, r: httpx.Response) -> Login:
    t = r.json()
    return Login(hub=hub, access_token=str(t["access_token"]), refresh_token=str(t["refresh_token"]),
                 expires_at=time.time() + float(t.get("expires_in") or 3600))  # fmt: skip


class Session:
    """The hub and its bearer token; with a saved OAuth login it refreshes the token when needed."""

    def __init__(self, hub: str, token: str, login: Login | None = None, warn: str | None = None) -> None:
        self.hub, self.token, self.login, self.warn = hub, token, login, warn

    def auth_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    def ensure_fresh(self) -> None:
        if self.login and self.login.get("expires_at", 0) - time.time() < REFRESH_MARGIN:
            self.refresh()

    def refresh(self) -> None:
        if not self.login:
            raise CliError(MSG_EXPIRED, AUTH)
        if self._adopt_rotated():
            return
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self.login["refresh_token"],
            "client_id": CLIENT_ID,
        }
        r = _post_form(self.hub, "/oauth/token", form)
        if r.status_code in (400, 401):
            raise CliError(MSG_EXPIRED, AUTH)
        if r.status_code != 200:
            raise CliError(f"hub respondeu HTTP {r.status_code} ao renovar a sessão do CLI", FAIL)
        self._use(_login_from(self.hub, r))

    def _adopt_rotated(self) -> bool:
        """Another `hub` process already rotated the pair: refreshing with ours would reuse a dead token."""
        # ponytail: two processes refreshing in the same instant can still collide; a file lock (or a short
        # reuse grace on the hub) closes that window if it shows up in practice.
        disk = load_login()
        ours = self.login.get("refresh_token") if self.login else None
        if disk.get("hub") != self.hub or disk.get("refresh_token") in (None, ours):
            return False
        self.login, self.token = disk, disk.get("access_token", "")
        return True

    def _use(self, login: Login) -> None:
        save_login(login)  # persisted before the new access token is used
        self.login, self.token = login, login["access_token"]

    def request(self, c: httpx.Client, method: str, url: str, **kw: Any) -> httpx.Response:
        """One request; a 401 on an OAuth login = expired/revoked access token: refresh once and retry."""
        extra = kw.pop("headers", None) or {}
        r = c.request(method, url, headers={**extra, **self.auth_headers()}, **kw)
        if r.status_code == 401 and self.login:
            self.refresh()
            r = c.request(method, url, headers={**extra, **self.auth_headers()}, **kw)
        return r


def resolve(hub: str | None, cli: str | None, file: Path | None = None, fallback_hub: str = "") -> Session:
    saved = load_login()
    h = pick_hub(hub, saved, fallback_hub)
    explicit = explicit_token(cli, file)
    if explicit:
        return Session(h, explicit[0], warn=explicit[1])
    if saved.get("hub") != h:  # a login saved for another hub is never sent here
        raise CliError(MSG_NO_AUTH, AUTH)
    if saved.get("refresh_token"):
        s = Session(h, saved.get("access_token", ""), saved)
        s.ensure_fresh()
        return s
    if saved.get("token"):
        return Session(h, saved["token"])
    raise CliError(MSG_NO_AUTH, AUTH)


def _start_device(hub: str) -> dict[str, Any]:
    r = _post_form(hub, "/oauth/device_authorization", {"client_id": CLIENT_ID, "scope": SCOPE})
    if r.status_code == 404:
        raise CliError(
            "este hub não tem o login pelo navegador (OAuth desligado): use um token pessoal "
            "(Minha conta › Acessos) com HUB_TOKEN ou `hub login --token-file`", AUTH)  # fmt: skip
    if r.status_code != 200:
        raise CliError(f"hub respondeu HTTP {r.status_code} em /oauth/device_authorization", FAIL)
    return r.json()


def _tell_user(d: dict[str, Any], open_browser: bool) -> None:
    complete, code = d.get("verification_uri_complete") or d["verification_uri"], d["user_code"]
    print(f"Abra {complete} e confirme o código {code}", file=sys.stderr)
    print(f"(ou abra {d['verification_uri']} e digite o código {code})", file=sys.stderr)
    if open_browser:
        with contextlib.suppress(webbrowser.Error):  # no browser (server, agent): the printed link is enough
            webbrowser.open(complete)
    print("Aguardando a aprovação no navegador…", file=sys.stderr)


def _poll(hub: str, d: dict[str, Any]) -> httpx.Response:
    interval = float(d.get("interval") or 5)
    deadline = time.monotonic() + float(d.get("expires_in") or 600)
    form = {"grant_type": DEVICE_GRANT, "device_code": d["device_code"], "client_id": CLIENT_ID}
    while time.monotonic() < deadline:
        time.sleep(interval)
        r = _post_form(hub, "/oauth/token", form)
        if r.status_code == 200:
            return r
        err = _oauth_error(r)
        if err == "slow_down":
            interval += 5
        elif err != "authorization_pending":
            raise CliError(DEVICE_FATAL.get(err, f"hub recusou o login: {err}"), AUTH)
    raise CliError(DEVICE_FATAL["expired_token"], AUTH)


def device_login(hub: str | None, open_browser: bool) -> dict[str, Any]:
    h = pick_hub(hub, load_login())
    d = _start_device(h)
    _tell_user(d, open_browser)
    r = _poll(h, d)
    p = save_login(_login_from(h, r))
    t = r.json()
    return {"ok": True, "hub": h, "user": t.get("user"), "scope": t.get("scope", SCOPE), "saved": str(p)}


def logout() -> dict[str, Any]:
    saved, p = load_login(), cred_path()
    if not p.exists():
        return {"ok": True, "hub": None, "removed": False}
    if saved.get("hub") and saved.get("refresh_token"):
        with contextlib.suppress(CliError):  # best effort: the local copy goes away even with the hub down
            _post_form(
                saved["hub"], "/oauth/revoke", {"client_id": CLIENT_ID, "token": saved["refresh_token"]}
            )
    p.unlink()
    return {"ok": True, "hub": saved.get("hub"), "removed": True}
