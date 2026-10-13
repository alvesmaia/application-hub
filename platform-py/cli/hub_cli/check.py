"""`hub check`: qualidade e contrato do app, local e sem Docker.

Etapas: ruff, ruff format, ty, openapi.json (gerado do código), pytest + cobertura, manifesto (mesmas regras do
pipeline do hub) e conformidade local (subconjunto do contrato contra o app de pé atrás do hub em processo).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from . import dev
from .errors import CliError

Step = tuple[str, Callable[[Path], tuple[bool, str]]]


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def _uv_raw(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["uv", "run", "-q", "--project", str(directory), *args]
    try:
        return subprocess.run(cmd, cwd=directory, capture_output=True, text=True, encoding="utf-8",  # noqa: S603
                              errors="replace", env=_env(), check=False, timeout=900)  # fmt: skip
    except FileNotFoundError as e:
        raise CliError("uv não encontrado no PATH (https://docs.astral.sh/uv/)", 1) from e


def _uv(directory: Path, *args: str) -> tuple[bool, str]:
    p = _uv_raw(directory, *args)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def _pkg(directory: Path) -> str:
    return str(_manifest(directory).get("id", "")).replace("-", "_")


def _manifest(directory: Path) -> dict[str, Any]:
    mf = directory / "manifest.json"
    if not mf.is_file():
        raise CliError(f"manifest.json não encontrado em {directory.resolve()}", 2)
    data = json.loads(mf.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CliError("manifest.json inválido", 2)
    return data


def manifest_problems(m: dict[str, Any]) -> list[str]:
    """Regras do passo Manifest do pipeline do hub (+ `requires`), sem precisar do hub."""
    from hub.models import requires_error

    out: list[str] = []
    mid = str(m.get("id", ""))
    if not re.match(r"^[a-z][a-z0-9-]{1,31}$", mid):
        out.append(f"id inválido: {mid!r}")
    if not m.get("name") or len(str(m["name"])) > 80:
        out.append("name obrigatório (até 80 caracteres)")
    if not re.match(r"^\d+\.\d+\.\d+([-+][\w.]+)?$", str(m.get("version", ""))):
        out.append("version deve ser semver (1.2.3)")
    routes = m.get("routes") or []
    if routes != [f"/{mid}/*"] and any(not str(r).startswith(f"/{mid}/") for r in routes):
        out.append(f"routes devem ficar sob /{mid}/")
    if not (m.get("api") or {}).get("enabled"):
        out.append("api.enabled deve ser true (API obrigatória, contrato §23)")
    err = requires_error(m.get("requires"))
    if err:
        out.append(err)
    return out


def step_manifest(directory: Path) -> tuple[bool, str]:
    probs = manifest_problems(_manifest(directory))
    return not probs, "; ".join(probs) or "ok"


def step_openapi(directory: Path) -> tuple[bool, str]:
    p = _uv_raw(directory, "python", "-m", _pkg(directory), "openapi")
    if p.returncode != 0:
        return False, (p.stdout + p.stderr)[-2000:]
    target = directory / "openapi.json"
    old = target.read_text(encoding="utf-8") if target.is_file() else ""
    doc = p.stdout
    json.loads(doc)
    if old.strip() != doc.strip():
        target.write_text(doc.strip() + "\n", encoding="utf-8", newline="\n")
        return True, "openapi.json atualizado (inclua no commit)"
    return True, "openapi.json em dia"


def _wait(url: str, seconds: float = 60) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _why(log: Path) -> str:
    """Última exceção do log do app (`.hub/check-app.log`): é ela que explica um 500 na conformidade."""
    try:
        lines = [
            ln.rstrip() for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()
        ]
    except OSError:
        return ""
    for ln in reversed(lines[-200:]):
        if m := re.match(r"^\w[\w.]*(Error|Exception|Warning)\b.*", ln):
            msg = m[0][:180]
            if "no such column" in msg or "no such table" in msg:
                msg += " — a migração mudou depois do primeiro `hub check`: apague `.hub/*.db` e rode de novo"
            return f" — {msg}"
    return f" (veja {log.name})" if lines else ""


def conformance(directory: Path, upstream: str, app_id: str, log: Path | None = None) -> list[str]:
    """Subconjunto do contrato (CONTRATO-APP §2, §3, §3.0, §12–13, §23) pelo hub em processo."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from starlette.testclient import TestClient

    devapp = dev.devapp_module()
    asgi, _ = devapp.dev_asgi(directory, upstream, "admin")
    fails: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    r = httpx.get(f"{upstream}/{app_id}/_hub/ready", timeout=5)
    need(r.status_code == 200, f"§2 GET /{app_id}/_hub/ready → {r.status_code} (esperado 200)")
    with TestClient(asgi) as c:
        page = c.get(f"/apps/{app_id}")
        need(page.status_code == 200 and "platform-header" in page.text, "§3 página no shell do hub")
        frag = c.get(f"/_frag/{app_id}/")
        body = frag.text
        need(frag.status_code == 200 and 'class="workspace' in body, "§3 fragmento com o workspace")
        need("<html" not in body.lower() and "<body" not in body.lower(), "§3 fragmento sem <html>/<body>")
        need("<script" not in body.lower() and not re.search(r"\son[a-z]+=", body), "§13 sem script/on*=")
        need(f'href="/{app_id}/' not in body and f'action="/{app_id}/' not in body, "§3.0 link sem /apps")
        need("no-store" in frag.headers.get("cache-control", ""), "§3 fragmento com Cache-Control: no-store")
        need(
            not any(k.startswith("x-hub-") for k in frag.headers), "§12 nenhum cabeçalho x-hub-* na resposta"
        )
        need(c.get(f"/_frag/{app_id}/secao-que-nao-existe").status_code == 404, "§3 seção desconhecida → 404")
    oa = httpx.get(f"{upstream}/{app_id}/api/openapi.json", timeout=5)
    try:
        doc = oa.json()
        paths = doc.get("paths", {})
        need(str(doc.get("openapi", "")).startswith("3."), "§23 OpenAPI 3.x")
        need(
            bool(paths) and all(p.startswith(f"/{app_id}/api/") for p in paths), "§23 caminhos sob /{id}/api/"
        )
        ops = [o for item in paths.values() for o in item.values() if isinstance(o, dict)]
        need(all(o.get("operationId") and o.get("responses") for o in ops), "§23 operationId e responses")
        for p, item in paths.items():
            if "get" in item and "{" not in p:
                code = httpx.get(f"{upstream}{p}", timeout=5).status_code
                need(code == 401, f"§23 {p} sem identidade → {code} (esperado 401)")
    except ValueError:
        fails.append(f"§23 /{app_id}/api/openapi.json não é JSON ({oa.status_code})")
    return fails


def step_conformance(directory: Path) -> tuple[bool, str]:
    app_id = str(_manifest(directory)["id"])
    port = dev.free_port()
    env = dev.app_env(directory, app_id, port)
    env["DATABASE_URL"] = f"sqlite:///{(directory.resolve() / '.hub' / 'check.db').as_posix()}"
    env["HUB_STORAGE_LOCAL_DIR"] = str(directory.resolve() / ".hub" / "check-storage")
    cmd = dev.app_command(directory, app_id.replace("-", "_"), port, reload=False)
    os.environ.setdefault("HUB_DATA_DIR", str(directory.resolve() / ".hub" / "hubdata"))
    log = directory.resolve() / ".hub" / "check-app.log"
    with log.open("w", encoding="utf-8") as out:
        proc = dev.spawn(cmd, directory, env, out)
        try:
            upstream = f"http://127.0.0.1:{port}"
            if not _wait(f"{upstream}/{app_id}/_hub/ready"):
                dev.stop(proc)
                return False, f"app não ficou pronto em 60 s: {log.read_text(encoding='utf-8')[-1500:]}"
            fails = conformance(directory, upstream, app_id, log)
            return not fails, "; ".join(fails) or "ok"
        finally:
            dev.stop(proc)


STEPS: list[Step] = [
    ("manifesto", step_manifest),
    ("ruff", lambda d: _uv(d, "ruff", "check", ".")),
    ("formato", lambda d: _uv(d, "ruff", "format", "--check", ".")),
    ("ty", lambda d: _uv(d, "ty", "check", "src")),
    ("openapi", step_openapi),
    ("pytest", lambda d: _uv(d, "pytest", "-q")),
    ("conformidade", step_conformance),
]


# Como corrigir, quando há um comando que resolve sozinho.
FIX = {"ruff": "uv run ruff check --fix .", "formato": "uv run ruff format ."}


def run(directory: Path, only: list[str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, fn in STEPS:
        if only and name not in only:
            continue
        t0 = time.monotonic()
        try:
            ok, out = fn(directory)
        except CliError:
            raise
        except Exception as e:  # uma etapa quebrada vira falha da etapa, não da CLI
            ok, out = False, f"{type(e).__name__}: {e}"
        if not ok and name in FIX:
            out = f"{out}\n\nPara corrigir: {FIX[name]}"
        results.append({"step": name, "ok": ok, "seconds": round(time.monotonic() - t0, 1), "detail": out})
    return results
