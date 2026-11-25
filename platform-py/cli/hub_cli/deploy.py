"""`hub login` and `hub deploy`: packages the app and sends it to the hub's deployments API.

The hub applies the manifest's `requires` (database, storage) and stores the variables/secrets sent
(write-only: values are never printed, logged or returned). Production stays pending approval.
"""

from __future__ import annotations

import getpass
import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from hub_cli import net

from . import check
from .auth import Session, resolve, save_login
from .errors import AUTH, FAIL, UNREACHABLE, VALIDATION, CliError
from .pack import pack

ENVS = {"staging": "staging", "production": "producao", "producao": "producao"}


def _client(hub: str) -> httpx.Client:
    return net.client(base_url=hub, timeout=900, headers={"X-Hub-Request": "1"})


def _call(c: httpx.Client, s: Session, method: str, url: str, **kw: Any) -> httpx.Response:
    try:
        r = s.request(c, method, url, **kw)
    except httpx.HTTPError as e:
        raise CliError(f"hub inacessível: {net.why(e)}", UNREACHABLE) from e
    if r.status_code in (401, 403):
        raise CliError(
            f"autenticação recusada pelo hub (HTTP {r.status_code}): confira o token e o escopo", AUTH
        )
    return r


def login(hub: str | None, token: str) -> dict[str, Any]:
    """Saves a personal token (CI): checked against the hub first. Without a token, `hub login` uses the
    device flow (auth.device_login)."""
    s = resolve(hub, token)
    with _client(s.hub) as c:
        r = _call(c, s, "GET", "/api/ops/deployments")
    if r.status_code != 200:
        raise CliError(f"hub respondeu HTTP {r.status_code} em /api/ops/deployments", FAIL)
    p = save_login({"hub": s.hub, "token": s.token})
    return {"ok": True, "hub": s.hub, "saved": str(p)}


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
    """Values for `requires.variables/secrets`: from the deploy file, otherwise prompts (secrets are not echoed).
    Empty = keep the current value in the hub. Never prints values."""
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


def _package(directory: Path, m: dict[str, Any]) -> tuple[str, str, bytes]:
    """Regenerates openapi.json (template projects) and packs the build context: (sha256, file name, bytes)."""
    if (directory / "src").is_dir():
        ok, detail = check.step_openapi(directory)
        if not ok:
            raise CliError(f"openapi.json não gerado: {detail[-500:]}", FAIL)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{m['id']}-{m['version']}.hubapp"
        sha = pack(directory, None, out, directory, "Dockerfile")
        return sha, out.name, out.read_bytes()


def _job(r: httpx.Response) -> dict[str, Any]:
    try:
        job = r.json()
    except ValueError:
        job = {"message": r.text[:300]}
    if not job.get("message"):  # hub refusal: the reason comes in `error` (e.g. package size)
        job["message"] = str(job.get("error") or "")[:300]
    return job


def _valid_manifest(directory: Path) -> dict[str, Any]:
    m = check._manifest(directory)
    probs = check.manifest_problems(m)
    if probs:
        raise CliError("manifesto inválido: " + "; ".join(probs), VALIDATION)
    return m


def deploy(directory: Path, env: str, hub: str | None, token: str | None, env_file: Path | None,
           tty: bool) -> dict[str, Any]:  # fmt: skip
    target = ENVS.get(env)
    if target is None:
        raise CliError("--env: use staging ou production", VALIDATION)
    m = _valid_manifest(directory)
    s = resolve(hub, token)
    env_file = env_file or directory / f"deploy.{env}.env"
    values = collect_values(m, env_file, tty)
    sha, name, data = _package(directory, m)
    form = {"env": target, "config": json.dumps(values)}
    with _client(s.hub) as c:
        r = _call(c, s, "POST", "/api/ops/packages", params={"env": target, "wait": "true"},
                  files={"package": (name, data, "application/zip")}, data=form)  # fmt: skip
    job = _job(r)
    ok = r.status_code in (200, 201) and bool(job.get("ok"))
    result = {"ok": ok, "app": m["id"], "version": m["version"], "env": env, "sha256": sha,
              "status": r.status_code, "message": job.get("message", ""), "provisioned": job.get("provisioned", {}),
              "variables": sorted(values["variables"]), "secrets": sorted(values["secrets"])}  # fmt: skip
    if target == "staging" and ok:
        result["url"] = f"{s.hub}/hub-env?to=staging&next=/apps/{m['id']}"
    if not ok:
        raise CliError(f"implantação recusada: {result['message'] or r.status_code}", FAIL)
    return result
