"""Content checks of a `.hubapp` package (manifest, checksums, OpenAPI, contract, front-end), used by
the steps in `hub.ops.pipeline`."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from hub.models import requires_error

SDK_MAJOR = "1"
ROOT_FILES = {
    "manifest.json",
    "runtime.json",
    "openapi.json",
    "checksums.txt",
    "sbom.spdx.json",
    "icon.svg",
    "signature",
}
SCRIPTS_CHECK = "App traz JavaScript próprio"


def sri_sha384(b: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(b).digest()).decode()


_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SCRIPT = re.compile(r"^ui/assets/[a-z0-9][a-z0-9._-]*\.m?js$")
_IMAGE = re.compile(r"^(?:[A-Za-z0-9.-]+(?::\d+)?/)?[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$")
_MEM = re.compile(r"^(\d+)(Ki|Mi|Gi)$")
_SECRET = [
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "chave privada PEM"),
    (re.compile(rb"AKIA[A-Z0-9]{16}"), "chave de acesso AWS"),
    (re.compile(rb"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_-]{40}"), "token do GitHub"),
    (re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10}"), "token do Slack"),
    (re.compile(rb"sk_live_[A-Za-z0-9]{16}"), "chave secreta de pagamento"),
    (re.compile(rb"AIza[A-Za-z0-9_-]{35}"), "chave de API do Google"),
]


def _is_secret_file(name: str) -> bool:
    b = name.rsplit("/", 1)[-1].lower()
    return (b == ".env" or b.startswith(".env.") or b.endswith((".pem", ".key", ".p12", ".pfx"))
            or b in (".npmrc", ".git-credentials")
            or any(b in (k, k + ".pub") for k in ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")))  # fmt: skip


def _inline_script(html: str) -> bool:
    return any("src=" not in m.group(0) for m in re.finditer(r"<script[^>]*>?", html, re.I))


def _inline_handler(html: str) -> bool:
    return any(
        "on" in m.group(0).lower() for m in re.finditer(r"<[^>]*[\s/\"']on[a-z]+\s*=[^>]*>", html, re.I)
    )


def _route_error(mid: str, routes: list[Any], registered: list[Any]) -> str:
    if not routes or any(not str(r).startswith(f"/{mid}/") or ".." in str(r) for r in routes):
        return f"rota inválida: {routes!r}"[:200]
    for a in registered:
        if a.id != mid and any(str(r).split("/")[1] == a.id for r in routes):
            return f"Conflito no registro: rota de {a.id}"
    return ""


def _policy_error(m: dict[str, Any]) -> str:
    """Resources, declared scripts and `requires`, checked in this order."""
    res = m.get("resources") or {"cpu": 0.5, "memory": "256Mi", "pids": 100}
    mm = _MEM.match(str(res.get("memory", "")))
    if not (0 < float(res.get("cpu", 0)) <= 4) or not mm or int(res.get("pids", 0)) not in range(1, 1025):
        return f"recursos fora da política: {res}"[:200]
    for s in m.get("scripts") or []:
        if (
            not _SCRIPT.match(str(s.get("path", "")))
            or not 10 <= len(str(s.get("reason", "")).strip()) <= 500
        ):
            return f"scripts: {s.get('path')!r} inválido (ui/assets/<nome>.js e motivo de 10–500 caracteres)"
    req_err = requires_error(m.get("requires"))
    return req_err[:200] if req_err else ""


def _manifest(text: str, registered: list[Any]) -> tuple[dict[str, Any] | None, str, bool]:
    """(manifesto, detalhe, ok)."""
    try:
        m = json.loads(text)
        if not isinstance(m, dict):
            raise ValueError
    except ValueError:
        return None, "manifest.json inválido (JSON)", False
    mid = str(m.get("id", ""))
    if not _ID.match(mid):
        return None, f"id inválido: {mid[:40]!r}", False
    if not m.get("name") or len(str(m["name"])) > 80:
        return None, "nome inválido", False
    routes = m.get("routes") or []
    err = _route_error(mid, routes, registered) or _policy_error(m)
    if err:
        return None, err, False
    known = any(a.id == mid for a in registered)
    perms = m.get("permissions") or []
    detail = (f"{mid} {m.get('version', '')} · {len(routes)} rota(s) · {len(perms)} permissão(ões) · "
              f"{'atualização de app registrado' if known else 'app novo'}")  # fmt: skip
    return m, detail, True


def _parse_checksums(sums: bytes) -> tuple[dict[str, str], str]:
    listed: dict[str, str] = {}
    for line in sums.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        if "  " not in line:
            return {}, f"linha inválida em checksums.txt: {line!r}"
        h, n = line.split("  ", 1)
        listed[n.strip()] = h.strip()
    return listed, ""


def _checksum_mismatch(files: dict[str, bytes], listed: dict[str, str]) -> str:
    for n, b in files.items():
        if n in ("checksums.txt", "signature"):
            continue
        if n not in listed:
            return f"{n} não consta em checksums.txt"
        if listed[n].lower() != hashlib.sha256(b).hexdigest():
            return f"checksum divergente em {n}"
    return next((f"{n} listado em checksums.txt mas ausente" for n in listed if n not in files), "")


def _checksums(files: dict[str, bytes]) -> tuple[int, str]:
    sums = files.get("checksums.txt")
    if sums is None:
        return 0, "checksums.txt ausente"
    listed, err = _parse_checksums(sums)
    err = err or _checksum_mismatch(files, listed)
    return (0, err) if err else (len(listed), "")


def _openapi_paths(raw: bytes) -> dict[str, Any]:
    """The document's `paths`, after checking its size, JSON, version and `info`."""
    if len(raw) > 1 << 20:
        raise ValueError(f"openapi.json com {len(raw) // 1024} KiB (máximo 1024 KiB)")
    try:
        v = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"openapi.json inválido: {e}") from e
    if not str(v.get("openapi", "")).startswith("3."):
        raise ValueError(f"openapi.json: campo openapi {str(v.get('openapi', ''))[:16]!r} (esperado 3.x)")
    if not v.get("info", {}).get("title") or not v.get("info", {}).get("version"):
        raise ValueError("openapi.json sem info.title/info.version")
    paths = v.get("paths") or {}
    if not paths:
        raise ValueError("openapi.json sem nenhuma operação (paths vazio)")
    return paths


def _count_operations(p: str, item: dict[str, Any]) -> tuple[int, int]:
    ops = mut = 0
    for m, op in item.items():
        if m not in ("get", "put", "post", "delete", "patch", "head", "options"):
            continue
        ops += 1
        if not op.get("responses"):
            raise ValueError(f"openapi.json: {m.upper()} {p} sem responses")
        if m not in ("get", "head") and op.get("x-hub-readonly") is not True:
            mut += 1
    return ops, mut


def _openapi(mid: str, raw: bytes) -> tuple[int, int]:
    ops = mut = 0
    for p, item in _openapi_paths(raw).items():
        if not p.startswith(f"/{mid}/api/") or ".." in p:
            raise ValueError(f"openapi.json: caminho {p} fora da API do app (use /{mid}/api/…)")
        o, m = _count_operations(p, item)
        ops, mut = ops + o, mut + m
    if not ops or ops > 200:
        raise ValueError(
            "openapi.json sem nenhuma operação"
            if not ops
            else f"openapi.json com {ops} operações (máximo 200)"
        )
    return ops, mut


def _secret_found(files: dict[str, bytes]) -> str:
    bad = next((f"arquivo de segredo no pacote: {n}" for n in files if _is_secret_file(n)), "")
    if bad:
        return bad
    for n, b in files.items():
        for rx, kind in _SECRET:
            if rx.search(b):
                return f"possível segredo ({kind}) em {n}"
    return ""


_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def build_tag(app: str, version: str) -> str:
    from hub.ops.runtime import prefix

    return f"{prefix()}{app}:{version}"


def _build_spec(m: dict[str, Any], spec: dict[str, Any], files: dict[str, bytes]) -> tuple[str, str]:
    """Package with a build context (`build/**`): the hub builds `app-hub-app-<id>:<version>`. (error, tag)."""
    df = spec.get("dockerfile", "Dockerfile")
    if not isinstance(df, str) or not df or df.startswith("/") or ".." in df.split("/") or "\\" in df:
        return "runtime.json: build.dockerfile inválido", ""
    if "build/" + df not in files:
        return f"runtime.json: Dockerfile build/{df} ausente do pacote", ""
    ver = str(m.get("version", ""))
    if not _TAG.match(ver):
        return f"versão {ver!r} não serve de tag de imagem (use letras, números, . _ -)", ""
    return "", build_tag(m["id"], ver)


def is_build(image: str) -> bool:
    return bool(image) and "@sha256:" not in image


def _contract_image(m: dict[str, Any], files: dict[str, bytes]) -> tuple[str, str]:
    """(error, image) from `runtime.json`: a pinned digest or a build context."""
    rt = files.get("runtime.json")
    if rt is None:
        return "runtime.json ausente", ""
    try:
        v = json.loads(rt)
    except ValueError as e:
        return f"runtime.json inválido: {e}", ""
    if isinstance(v, dict) and isinstance(v.get("build"), dict):
        return _build_spec(m, v["build"], files)
    image = v.get("image") if isinstance(v, dict) else None
    if not isinstance(image, str):
        return "runtime.json sem o campo image (ou build)", ""
    if not _IMAGE.match(image):
        return f"imagem sem digest imutável (use nome@sha256:<64 hex>): {image}", ""
    return "", image


def _jobs_error(m: dict[str, Any], jobs: list[Any]) -> str:
    if "sdk" in m and str(m["sdk"]).split(".")[0] != SDK_MAJOR:
        return f"versão do SDK incompatível: {m['sdk']} (suportada: {SDK_MAJOR}.x)"
    for j in jobs:
        if not str(j.get("name", "")).startswith(m["id"] + ".") or int(j.get("max_duration_s", 0)) > 86400 \
                or int(j.get("max_attempts", 0)) > 20:  # fmt: skip
            return f"contrato de tarefas: {j.get('name')!r} fora da política"
    if jobs and "sdk" not in m:
        return (f"o manifesto declara {len(jobs)} tarefa(s) duráveis mas não declara o sdk "
                f'(ex.: "sdk": "{SDK_MAJOR}.0"): tarefas exigem o hub-sdk')  # fmt: skip
    return ""


def _contract_api(
    m: dict[str, Any], files: dict[str, bytes], allow_without_api: bool
) -> tuple[str, bool, str]:
    """(error, warn, detail text) for the declared API and MCP."""
    api, mcp = m.get("api") or {}, m.get("mcp") or {}
    warn = False
    if api.get("enabled", True):
        doc = files.get("openapi.json")
        if doc is None:
            return ("API obrigatória: o pacote não traz openapi.json (gere o documento do app) ou "
                    "declare api.enabled = false (só aceito se a plataforma permitir)"), False, ""  # fmt: skip
        try:
            n, d = _openapi(m["id"], doc)
        except ValueError as e:
            return str(e), False, ""
        api_txt = (
            f" · API {api.get('base', '/api/v1')} ({n} operações, {d} mutações — no MCP exigem aprovação)"
        )
    elif allow_without_api:
        warn, api_txt = True, " · SEM API (exceção permitida pela plataforma)"
    else:
        return ("API obrigatória: o manifesto declara api.enabled = false e a plataforma não permite "
                "apps sem API ([apps].allow_without_api = false)"), False, ""  # fmt: skip
    if mcp.get("enabled") and not api.get("enabled", True):
        return "mcp.enabled exige a API (as ferramentas MCP são geradas do OpenAPI)", False, ""
    mcp_txt = (" · MCP ligado (initialize + tools/list conferidos no candidato antes da troca)"
               if mcp.get("enabled") else " · MCP desligado")  # fmt: skip
    return "", warn, api_txt + mcp_txt


def _contract(m: dict[str, Any], files: dict[str, bytes], allow_without_api: bool) -> tuple[str, str, str]:
    err, image = _contract_image(m, files)
    if err:
        return "failed", err, ""
    jobs = m.get("jobs") or []
    err = _jobs_error(m, jobs)
    if err:
        return "failed", err, ""
    mode = "web_component" if m.get("mode") == "web_component" else "fragment"
    err, warn, api_mcp_txt = _contract_api(m, files, allow_without_api)
    if err:
        return "failed", err, ""
    ready = f" · prontidão GET /{m['id']}/_hub/ready exigida no candidato (sem a rota: recusado)"
    jobs_txt = (" · tarefas: " + ", ".join(f"{j['name']} ({j['max_duration_s']} s, {j['max_attempts']}×)" for j in jobs)
                if jobs else "")  # fmt: skip
    stack = (
        f" · stack {m['runtime']['stack']}"
        if isinstance(m.get("runtime"), dict) and m["runtime"].get("stack")
        else ""
    )
    src = "imagem construída no hub a partir de build/" if is_build(image) else "imagem por digest"
    det = f"{src} · modo {mode} · entrada {m['routes'][0]}{stack}{jobs_txt}{api_mcp_txt}{ready}"
    return ("warn" if warn else "ok"), det, image


def _html_error(n: str, b: bytes) -> str:
    t = b.decode(errors="replace")
    if _inline_script(t):
        return f"script inline em {n} (incompatível com a CSP)"
    if _inline_handler(t):
        return (f"atributo de evento inline (on…=) em {n}: incompatível com a CSP — "
                "use os atributos data-hub-* do hub")  # fmt: skip
    if "javascript:" in t.lower():
        return f"URL javascript: em {n}"
    return ""


def _scan_files(files: dict[str, bytes]) -> tuple[str, int, int]:
    """(error, HTML count, CSS count) over the package's non-build files."""
    html = css = 0
    for n, b in files.items():
        if n.startswith("build/"):
            continue  # build context: not front-end
        in_ui = n.startswith("ui/")
        if not in_ui and n not in ROOT_FILES:
            return f"arquivo fora de ui/: {n}", 0, 0
        low = n.lower()
        if low.endswith((".css", ".js")) and not in_ui:
            return f"CSS/JS fora de ui/: {n}", 0, 0
        css += low.endswith(".css")
        if low.endswith((".html", ".htm")):
            html += 1
            err = _html_error(n, b)
            if err:
                return err, 0, 0
    return "", html, css


def _script_reviews(files: dict[str, bytes], declared: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    for n in files:
        if n.lower().endswith((".js", ".mjs")) and n not in declared and not n.startswith("build/"):
            return (f"JavaScript não declarado: {n} — apps não trazem JavaScript (use os atributos "
                    "data-hub-*); exceções vão em manifest.scripts com o motivo e passam por revisão"), []  # fmt: skip
    reviews: list[dict[str, str]] = []
    for p, s in declared.items():
        if p not in files:
            return f"manifest.scripts declara {p} mas o arquivo não está no pacote", []
        reviews.append({"path": p, "reason": s["reason"].strip(), "sha384": sri_sha384(files[p])})
    return "", reviews


def _frontend(m: dict[str, Any], files: dict[str, bytes]) -> tuple[str, str, list[dict[str, str]]]:
    declared = {s["path"]: s for s in m.get("scripts") or []}
    err, html, css = _scan_files(files)
    reviews: list[dict[str, str]] = []
    if not err:
        err, reviews = _script_reviews(files, declared)
    if err:
        return "failed", err, []
    base = f"{sum(n.startswith('ui/') for n in files)} arquivo(s) em ui/ · {html} HTML · {css} CSS"
    if not reviews:
        return "ok", f"{base} · sem JavaScript próprio", []
    lst = "; ".join(f"{r['path']} ({r['sha384']}) — motivo: {r['reason']}" for r in reviews)
    return "warn", (f"{base} · REVISE: {SCRIPTS_CHECK} — exige aprovação explícita da administração (revisão de "
                    f"segurança, separada da aprovação da implantação; hash novo = nova aprovação): {lst}"), reviews  # fmt: skip
