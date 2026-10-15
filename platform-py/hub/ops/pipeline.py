"""Validation pipeline for `.hubapp` packages (7 steps, like `hub-ops::pipeline`) and defensive reading of the zip."""

from __future__ import annotations

import hashlib as hashlib  # deploy_jobs reaches it as pl.hashlib
import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# The checks live in package_checks; callers and tests keep reaching them through this module.
from hub.ops.package_checks import SCRIPTS_CHECK as SCRIPTS_CHECK
from hub.ops.package_checks import _build_spec as _build_spec
from hub.ops.package_checks import _checksums, _contract, _frontend, _secret_found
from hub.ops.package_checks import _inline_handler as _inline_handler
from hub.ops.package_checks import _inline_script as _inline_script
from hub.ops.package_checks import _is_secret_file as _is_secret_file
from hub.ops.package_checks import _manifest as _manifest
from hub.ops.package_checks import _openapi as _openapi
from hub.ops.package_checks import build_tag as build_tag
from hub.ops.package_checks import is_build as is_build
from hub.ops.package_checks import sri_sha384 as sri_sha384

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_ENTRIES, MAX_TOTAL, MAX_RATIO = 2000, 64 * 1024 * 1024, 200
STEPS = [
    ("Manifest", "Schema, rotas sem conflito e permissões declaradas"),
    ("Assinatura", "Pacote e imagem assinados por publicador autorizado (cosign)"),
    ("Segurança", "Scan de vulnerabilidades, SBOM e ausência de segredos"),
    ("Contrato", "Prontidão (/_hub/ready), rota de entrada, SDK compatível, API (OpenAPI) e MCP"),
    ("Front-end", "CSS escopado, sem globais JS, CSP compatível"),
    ("Smoke test", "Sobe em staging e o hub renderiza a rota de entrada"),
    ("Aprovação", "Gate manual para produção e registro em auditoria"),
]
Probe = Callable[[str], Awaitable[int]]


def step(i: int) -> dict[str, Any]:
    return {"name": STEPS[i][0], "description": STEPS[i][1], "state": "pending", "detail": ""}


def read_zip(data: bytes) -> dict[str, bytes]:  # noqa: C901, PLR0915
    """In-memory entries; refuses zip-slip, links, duplicates and zip bombs."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"arquivo zip inválido: {e}") from e
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError(f"pacote com entradas demais ({len(infos)} > {MAX_ENTRIES})")
    out: dict[str, bytes] = {}
    total = 0
    for i in infos:
        n = i.filename.removesuffix("/")
        parts = n.split("/")
        if (not n or len(n) > 512 or i.filename.startswith("/") or "\\" in n or ":" in n
                or any(ord(c) < 32 for c in n) or any(p in ("", ".", "..") for p in parts)):  # fmt: skip
            raise ValueError(f"entrada com caminho inseguro: {i.filename!r}")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"link simbólico não permitido: {n}")
        if i.is_dir():
            continue
        if i.file_size > 1 << 20 and i.file_size // max(i.compress_size, 1) > MAX_RATIO:
            raise ValueError(f"taxa de compressão suspeita em {n} (possível zip bomb)")
        total += i.file_size
        if total > MAX_TOTAL:
            raise ValueError("conteúdo descompactado excede o limite permitido")
        if n in out:
            raise ValueError(f"entrada duplicada: {n}")
        out[n] = z.read(i)
    return out


def peek_app_id(data: bytes) -> str | None:
    try:
        v = json.loads(read_zip(data)["manifest.json"])
        return str(v["id"])
    except (ValueError, KeyError, TypeError):
        return None


@dataclass
class _Run:
    """One pipeline run: its inputs and what the earlier steps found."""

    env: str
    registered: list[Any]
    probe: Probe | None
    allow_without_api: bool
    files: dict[str, bytes] = field(default_factory=dict)
    open_err: str = ""
    manifest: dict[str, Any] | None = None
    image: str = ""
    scripts: list[dict[str, str]] = field(default_factory=list)


def _step_manifest(r: _Run) -> tuple[str, str]:
    if r.open_err:
        return "failed", f"Pacote ilegível: {r.open_err}"
    if "manifest.json" not in r.files:
        return "failed", "manifest.json ausente ou não é UTF-8"
    r.manifest, det, good = _manifest(r.files["manifest.json"].decode(errors="replace"), r.registered)
    return ("ok" if good else "failed"), det


def _step_signature(r: _Run) -> tuple[str, str]:
    n, err = _checksums(r.files)
    if err:
        return "failed", err
    return "ok", f"{n} arquivos conferidos em checksums.txt · " + (
        "assinatura presente, verificação cosign não configurada" if "signature" in r.files
        else "sem assinatura (cosign não configurado)")  # fmt: skip


def _step_security(r: _Run) -> tuple[str, str]:
    bad = _secret_found(r.files)
    if bad:
        return "failed", bad
    if "sbom.spdx.json" in r.files:
        return (
            "ok",
            f"{len(r.files)} arquivos sem segredos · SBOM presente · scan de vulnerabilidades não configurado",
        )
    return "warn", f"{len(r.files)} arquivos sem segredos · SBOM ausente (sbom.spdx.json)"


def _step_contract(r: _Run, m: dict[str, Any]) -> tuple[str, str]:
    st, det, r.image = _contract(m, r.files, r.allow_without_api)
    return st, det


def _step_frontend(r: _Run, m: dict[str, Any]) -> tuple[str, str]:
    st, det, r.scripts = _frontend(m, r.files)
    return st, det


async def _step_smoke(r: _Run, m: dict[str, Any]) -> tuple[str, str]:
    app = next((a for a in r.registered if a.id == m["id"]), None)
    if app is None or r.probe is None:
        return "skipped", "pulado: app sem upstream registrado"
    try:
        code = await r.probe(f"{app.upstream.rstrip('/')}/{app.id}")
    except Exception as e:
        return "failed", f"upstream indisponível ({e})"
    return (
        ("ok", f"GET /{app.id} → {code}") if code < 500 else ("failed", f"rota de entrada respondeu {code}")
    )


def _step_approval(r: _Run) -> tuple[str, str]:
    return (
        ("warn", "Aguardando aprovação")
        if r.env == "producao"
        else ("ok", "Aprovado automaticamente (staging)")
    )


async def _run_step(i: int, r: _Run) -> tuple[str, str]:
    plain = {0: _step_manifest, 1: _step_signature, 2: _step_security, 6: _step_approval}
    if i in plain:
        return plain[i](r)
    m = r.manifest
    if m is None:
        return "ok", ""
    if i == 5:
        return await _step_smoke(r, m)
    return (_step_contract if i == 3 else _step_frontend)(r, m)


async def run_all(  # noqa: PLR0915
    data: bytes, env: str, registered: list[Any], probe: Probe | None = None, allow_without_api: bool = False,
    on_step: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Runs the 7 steps. `env` = `staging`/`producao`. Returns the report (`passed`, `reason`, `steps`, …)."""
    steps = [step(i) for i in range(len(STEPS))]
    r = _Run(env, registered, probe, allow_without_api)
    try:
        r.files = read_zip(data)
    except ValueError as e:
        r.open_err = str(e)
    reason = ""

    def emit() -> None:
        if on_step:
            on_step(steps)

    for i in range(len(STEPS)):
        steps[i]["state"] = "running"
        emit()
        st, det = await _run_step(i, r)
        steps[i]["state"], steps[i]["detail"] = st, det
        if st == "failed":
            reason = f"{STEPS[i][0]}: {det}"
            for s in steps[i + 1 :]:
                s["state"], s["detail"] = "skipped", "não executado"
            emit()
            break
        emit()
    manifest = r.manifest
    return {
        "steps": steps, "passed": not reason, "reason": reason, "app": (manifest or {}).get("id", ""),
        "version": str((manifest or {}).get("version", "")), "image": r.image, "build": is_build(r.image),
        "scripts": r.scripts, "manifest": manifest,
    }  # fmt: skip


def build_context(files: dict[str, bytes], dockerfile: str = "Dockerfile") -> dict[str, bytes]:
    """Files under `build/**` minus what the package's `build/.dockerignore` excludes (the Dockerfile and the
    `.dockerignore` itself always stay, as in `docker build`)."""
    from docker.utils.build import PatternMatcher

    raw = files.get("build/.dockerignore", b"").decode("utf-8", "replace")
    pats = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not pats:
        return files
    pm = PatternMatcher(pats)
    keep = {"build/" + dockerfile, "build/.dockerignore"}
    return {
        n: b for n, b in files.items() if not n.startswith("build/") or n in keep or not pm.matches(n[6:])
    }
