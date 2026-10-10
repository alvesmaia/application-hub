"""`hub login` e `hub deploy`: empacota o app e envia à API de implantações do hub.

O hub aplica o `requires` do manifesto (banco, armazenamento) e grava as variáveis/segredos enviados
(só escrita: valores nunca são impressos, registrados nem devolvidos). Produção fica pendente de aprovação.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from . import check
from .errors import AUTH, FAIL, UNREACHABLE, VALIDATION, CliError
from .mcp import check_hub_url
from .pack import pack

ENVS = {"staging": "staging", "production": "producao", "producao": "producao"}


def cred_path() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "hub-cli" / "credentials.json"


def save_login(hub: str, token: str) -> Path:
    p = cred_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"hub": hub.rstrip("/"), "token": token}), encoding="utf-8")
    if os.name != "nt":
        p.chmod(0o600)
    return p


def load_login() -> dict[str, str]:
    p = cred_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in data.items() if k in ("hub", "token")} if isinstance(data, dict) else {}


def resolve(hub: str | None, token: str | None) -> tuple[str, str]:
    saved = load_login()
    h = (hub or os.environ.get("HUB_URL") or saved.get("hub") or "").rstrip("/")
    t = token or os.environ.get("HUB_TOKEN") or saved.get("token") or ""
    if not h:
        raise CliError("informe o hub: --hub URL ou `hub login`", VALIDATION)
    try:
        check_hub_url(h)
    except ValueError as e:
        raise CliError(str(e), VALIDATION) from e
    if not t:
        raise CliError("sem token: use `hub login`, HUB_TOKEN ou --token", AUTH)
    return h, t


def _client(hub: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=hub, timeout=900, headers={"Authorization": f"Bearer {token}", "X-Hub-Request": "1"}
    )


def _call(c: httpx.Client, method: str, url: str, **kw: Any) -> httpx.Response:
    try:
        r = c.request(method, url, **kw)
    except httpx.HTTPError as e:
        raise CliError(f"hub inacessível ({type(e).__name__})", UNREACHABLE) from e
    if r.status_code in (401, 403):
        raise CliError(
            f"autenticação recusada pelo hub (HTTP {r.status_code}): confira o token e o escopo", AUTH
        )
    return r


def login(hub: str, token: str | None) -> dict[str, Any]:
    token = token or os.environ.get("HUB_TOKEN")
    if not token:
        if not sys.stdin.isatty():
            raise CliError("sem TTY: passe o token por HUB_TOKEN ou --token", VALIDATION)
        token = getpass.getpass("Token do hub (não aparece na tela): ").strip()
    h, t = resolve(hub, token)
    with _client(h, t) as c:
        r = _call(c, "GET", "/api/ops/deployments")
    if r.status_code != 200:
        raise CliError(f"hub respondeu HTTP {r.status_code} em /api/ops/deployments", FAIL)
    p = save_login(h, t)
    return {"ok": True, "hub": h, "saved": str(p)}


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition("=")
        if not sep:
            raise CliError(f"linha inválida em {path.name} (esperado NOME=valor)", VALIDATION)
        out[k.strip()] = v.strip()
    return out


def collect_values(manifest: dict[str, Any], env_file: Path | None, tty: bool) -> dict[str, dict[str, str]]:
    """Valores de `requires.variables/secrets`: arquivo de implantação, senão pergunta (segredo sem eco).
    Vazio = mantém o valor atual no hub. Nunca imprime valores."""
    req = manifest.get("requires") or {}
    file_vals = read_env_file(env_file) if env_file and env_file.is_file() else {}
    names = {"variables": list(req.get("variables") or []), "secrets": list(req.get("secrets") or [])}
    unknown = sorted(set(file_vals) - set(names["variables"]) - set(names["secrets"]))
    if unknown:
        raise CliError(f"{', '.join(unknown)} não declarado(s) em requires.variables/secrets", VALIDATION)
    out: dict[str, dict[str, str]] = {"variables": {}, "secrets": {}}
    for kind, keys in names.items():
        for k in keys:
            v = file_vals.get(k)
            if v is None and tty:
                label = f"{'Segredo' if kind == 'secrets' else 'Variável'} {k} (vazio = manter o do hub): "
                v = getpass.getpass(label) if kind == "secrets" else input(label)
            if v:
                out[kind][k] = v
    return out


def deploy(directory: Path, env: str, hub: str | None, token: str | None, env_file: Path | None,
           tty: bool) -> dict[str, Any]:  # fmt: skip
    target = ENVS.get(env)
    if target is None:
        raise CliError("--env: use staging ou production", VALIDATION)
    m = check._manifest(directory)
    probs = check.manifest_problems(m)
    if probs:
        raise CliError("manifesto inválido: " + "; ".join(probs), VALIDATION)
    h, t = resolve(hub, token)
    env_file = env_file or directory / f"deploy.{env}.env"
    values = collect_values(m, env_file, tty)
    if (directory / "src").is_dir():
        ok, detail = check.step_openapi(directory)
        if not ok:
            raise CliError(f"openapi.json não gerado: {detail[-500:]}", FAIL)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{m['id']}-{m['version']}.hubapp"
        sha = pack(directory, None, out, directory, "Dockerfile")
        data = out.read_bytes()
    form = {"env": target, "config": json.dumps(values)}
    with _client(h, t) as c:
        r = _call(c, "POST", "/api/ops/packages", params={"env": target, "wait": "true"},
                  files={"package": (out.name, data, "application/zip")}, data=form)  # fmt: skip
    try:
        job = r.json()
    except ValueError:
        job = {"message": r.text[:300]}
    ok = r.status_code in (200, 201) and bool(job.get("ok"))
    result = {"ok": ok, "app": m["id"], "version": m["version"], "env": env, "sha256": sha,
              "status": r.status_code, "message": job.get("message", ""), "provisioned": job.get("provisioned", {}),
              "variables": sorted(values["variables"]), "secrets": sorted(values["secrets"])}  # fmt: skip
    if target == "staging" and ok:
        result["url"] = f"{h}/hub-env?to=staging&next=/apps/{m['id']}"
    if not ok:
        raise CliError(f"implantação recusada: {result['message'] or r.status_code}", FAIL)
    return result
