"""Pipeline de validação de pacotes `.hubapp` (7 passos, como o `hub-ops::pipeline`) e leitura defensiva do zip."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Awaitable, Callable
from typing import Any

from hub.models import requires_error

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_ENTRIES, MAX_TOTAL, MAX_RATIO = 2000, 64 * 1024 * 1024, 200
SDK_MAJOR = "1"
STEPS = [
    ("Manifest", "Schema, rotas sem conflito e permissões declaradas"),
    ("Assinatura", "Pacote e imagem assinados por publicador autorizado (cosign)"),
    ("Segurança", "Scan de vulnerabilidades, SBOM e ausência de segredos"),
    ("Contrato", "Prontidão (/_hub/ready), rota de entrada, SDK compatível, API (OpenAPI) e MCP"),
    ("Front-end", "CSS escopado, sem globais JS, CSP compatível"),
    ("Smoke test", "Sobe em staging e o hub renderiza a rota de entrada"),
    ("Aprovação", "Gate manual para produção e registro em auditoria"),
]
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
Probe = Callable[[str], Awaitable[int]]


def step(i: int) -> dict[str, Any]:
    return {"name": STEPS[i][0], "description": STEPS[i][1], "state": "pending", "detail": ""}


def read_zip(data: bytes) -> dict[str, bytes]:
    """Entradas em memória; recusa zip-slip, links, duplicatas e zip bombs."""
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
    if not routes or any(not str(r).startswith(f"/{mid}/") or ".." in str(r) for r in routes):
        return None, f"rota inválida: {routes!r}"[:200], False
    for a in registered:
        if a.id != mid and any(str(r).split("/")[1] == a.id for r in routes):
            return None, f"Conflito no registro: rota de {a.id}", False
    res = m.get("resources") or {"cpu": 0.5, "memory": "256Mi", "pids": 100}
    mm = _MEM.match(str(res.get("memory", "")))
    if not (0 < float(res.get("cpu", 0)) <= 4) or not mm or int(res.get("pids", 0)) not in range(1, 1025):
        return None, f"recursos fora da política: {res}"[:200], False
    for s in m.get("scripts") or []:
        if (
            not _SCRIPT.match(str(s.get("path", "")))
            or not 10 <= len(str(s.get("reason", "")).strip()) <= 500
        ):
            return (
                None,
                f"scripts: {s.get('path')!r} inválido (ui/assets/<nome>.js e motivo de 10–500 caracteres)",
                False,
            )
    req_err = requires_error(m.get("requires"))
    if req_err:
        return None, req_err[:200], False
    known = any(a.id == mid for a in registered)
    perms = m.get("permissions") or []
    detail = (f"{mid} {m.get('version', '')} · {len(routes)} rota(s) · {len(perms)} permissão(ões) · "
              f"{'atualização de app registrado' if known else 'app novo'}")  # fmt: skip
    return m, detail, True


def _checksums(files: dict[str, bytes]) -> tuple[int, str]:
    sums = files.get("checksums.txt")
    if sums is None:
        return 0, "checksums.txt ausente"
    listed: dict[str, str] = {}
    for line in sums.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        if "  " not in line:
            return 0, f"linha inválida em checksums.txt: {line!r}"
        h, n = line.split("  ", 1)
        listed[n.strip()] = h.strip()
    for n, b in files.items():
        if n in ("checksums.txt", "signature"):
            continue
        if n not in listed:
            return 0, f"{n} não consta em checksums.txt"
        if listed[n].lower() != hashlib.sha256(b).hexdigest():
            return 0, f"checksum divergente em {n}"
    for n in listed:
        if n not in files:
            return 0, f"{n} listado em checksums.txt mas ausente"
    return len(listed), ""


def _openapi(mid: str, raw: bytes) -> tuple[int, int]:
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
    ops = mut = 0
    for p, item in paths.items():
        if not p.startswith(f"/{mid}/api/") or ".." in p:
            raise ValueError(f"openapi.json: caminho {p} fora da API do app (use /{mid}/api/…)")
        for m, op in item.items():
            if m not in ("get", "put", "post", "delete", "patch", "head", "options"):
                continue
            ops += 1
            if not op.get("responses"):
                raise ValueError(f"openapi.json: {m.upper()} {p} sem responses")
            if m not in ("get", "head") and op.get("x-hub-readonly") is not True:
                mut += 1
    if not ops or ops > 200:
        raise ValueError(
            "openapi.json sem nenhuma operação"
            if not ops
            else f"openapi.json com {ops} operações (máximo 200)"
        )
    return ops, mut


async def run_all(
    data: bytes, env: str, registered: list[Any], probe: Probe | None = None, allow_without_api: bool = False,
    on_step: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Roda os 7 passos. `env` = `staging`/`producao`. Devolve o relatório (`passed`, `reason`, `steps`, …)."""
    steps = [step(i) for i in range(len(STEPS))]
    scripts: list[dict[str, str]] = []
    files: dict[str, bytes] = {}
    open_err = ""
    try:
        files = read_zip(data)
    except ValueError as e:
        open_err = str(e)
    manifest: dict[str, Any] | None = None
    image = ""
    reason = ""

    def emit() -> None:
        if on_step:
            on_step(steps)

    for i in range(len(STEPS)):
        steps[i]["state"] = "running"
        emit()
        st, det = "ok", ""
        if i == 0:
            if open_err:
                st, det = "failed", f"Pacote ilegível: {open_err}"
            elif "manifest.json" not in files:
                st, det = "failed", "manifest.json ausente ou não é UTF-8"
            else:
                manifest, det, good = _manifest(files["manifest.json"].decode(errors="replace"), registered)
                st = "ok" if good else "failed"
        elif i == 1:
            n, err = _checksums(files)
            st, det = ("failed", err) if err else (
                "ok", f"{n} arquivos conferidos em checksums.txt · "
                + ("assinatura presente, verificação cosign não configurada" if "signature" in files
                   else "sem assinatura (cosign não configurado)"))  # fmt: skip
        elif i == 2:
            bad = next((f"arquivo de segredo no pacote: {n}" for n in files if _is_secret_file(n)), "")
            for n, b in files.items():
                if bad:
                    break
                for rx, kind in _SECRET:
                    if rx.search(b):
                        bad = f"possível segredo ({kind}) em {n}"
                        break
            if bad:
                st, det = "failed", bad
            elif "sbom.spdx.json" in files:
                det = f"{len(files)} arquivos sem segredos · SBOM presente · scan de vulnerabilidades não configurado"
            else:
                st, det = "warn", f"{len(files)} arquivos sem segredos · SBOM ausente (sbom.spdx.json)"
        elif i == 3 and manifest is not None:
            st, det, image = _contract(manifest, files, allow_without_api)
        elif i == 4 and manifest is not None:
            st, det, scripts = _frontend(manifest, files)
        elif i == 5 and manifest is not None:
            app = next((a for a in registered if a.id == manifest["id"]), None)
            if app is None or probe is None:
                st, det = "skipped", "pulado: app sem upstream registrado"
            else:
                try:
                    code = await probe(f"{app.upstream.rstrip('/')}/{app.id}")
                    st, det = ("ok", f"GET /{app.id} → {code}") if code < 500 else (
                        "failed", f"rota de entrada respondeu {code}")  # fmt: skip
                except Exception as e:
                    st, det = "failed", f"upstream indisponível ({e})"
        elif i == 6:
            st, det = ("warn", "Aguardando aprovação") if env == "producao" else (
                "ok", "Aprovado automaticamente (staging)")  # fmt: skip
        steps[i]["state"], steps[i]["detail"] = st, det
        if st == "failed":
            reason = f"{STEPS[i][0]}: {det}"
            for s in steps[i + 1 :]:
                s["state"], s["detail"] = "skipped", "não executado"
            emit()
            break
        emit()
    return {
        "steps": steps, "passed": not reason, "reason": reason, "app": (manifest or {}).get("id", ""),
        "version": str((manifest or {}).get("version", "")), "image": image, "build": is_build(image),
        "scripts": scripts, "manifest": manifest,
    }  # fmt: skip


_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def build_context(files: dict[str, bytes], dockerfile: str = "Dockerfile") -> dict[str, bytes]:
    """Arquivos de `build/**` sem o que o `build/.dockerignore` do pacote exclui (o Dockerfile e o próprio
    `.dockerignore` sempre ficam, como no `docker build`)."""
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


def build_tag(app: str, version: str) -> str:
    from hub.ops.runtime import prefix

    return f"{prefix()}{app}:{version}"


def _build_spec(m: dict[str, Any], spec: dict[str, Any], files: dict[str, bytes]) -> tuple[str, str]:
    """Pacote com contexto de build (`build/**`): o hub constrói `app-hub-app-<id>:<versão>`. (erro, tag)."""
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


def _contract(m: dict[str, Any], files: dict[str, bytes], allow_without_api: bool) -> tuple[str, str, str]:
    rt = files.get("runtime.json")
    if rt is None:
        return "failed", "runtime.json ausente", ""
    try:
        v = json.loads(rt)
    except ValueError as e:
        return "failed", f"runtime.json inválido: {e}", ""
    if isinstance(v, dict) and isinstance(v.get("build"), dict):
        err, image = _build_spec(m, v["build"], files)
        if err:
            return "failed", err, ""
    else:
        image = v.get("image") if isinstance(v, dict) else None
        if not isinstance(image, str):
            return "failed", "runtime.json sem o campo image (ou build)", ""
        if not _IMAGE.match(image):
            return "failed", f"imagem sem digest imutável (use nome@sha256:<64 hex>): {image}", ""
    if "sdk" in m and str(m["sdk"]).split(".")[0] != SDK_MAJOR:
        return "failed", f"versão do SDK incompatível: {m['sdk']} (suportada: {SDK_MAJOR}.x)", ""
    jobs = m.get("jobs") or []
    for j in jobs:
        if not str(j.get("name", "")).startswith(m["id"] + ".") or int(j.get("max_duration_s", 0)) > 86400 \
                or int(j.get("max_attempts", 0)) > 20:  # fmt: skip
            return "failed", f"contrato de tarefas: {j.get('name')!r} fora da política", ""
    if jobs and "sdk" not in m:
        return "failed", (f"o manifesto declara {len(jobs)} tarefa(s) duráveis mas não declara o sdk "
                          f'(ex.: "sdk": "{SDK_MAJOR}.0"): tarefas exigem o hub-sdk'), ""  # fmt: skip
    mode = "web_component" if m.get("mode") == "web_component" else "fragment"
    api, mcp = m.get("api") or {}, m.get("mcp") or {}
    warn = False
    if api.get("enabled", True):
        doc = files.get("openapi.json")
        if doc is None:
            return "failed", ("API obrigatória: o pacote não traz openapi.json (gere o documento do app) ou "
                              "declare api.enabled = false (só aceito se a plataforma permitir)"), ""  # fmt: skip
        try:
            n, d = _openapi(m["id"], doc)
        except ValueError as e:
            return "failed", str(e), ""
        api_txt = (
            f" · API {api.get('base', '/api/v1')} ({n} operações, {d} mutações — no MCP exigem aprovação)"
        )
    elif allow_without_api:
        warn, api_txt = True, " · SEM API (exceção permitida pela plataforma)"
    else:
        return "failed", ("API obrigatória: o manifesto declara api.enabled = false e a plataforma não permite "
                          "apps sem API ([apps].allow_without_api = false)"), ""  # fmt: skip
    if mcp.get("enabled") and not api.get("enabled", True):
        return "failed", "mcp.enabled exige a API (as ferramentas MCP são geradas do OpenAPI)", ""
    mcp_txt = (" · MCP ligado (initialize + tools/list conferidos no candidato antes da troca)"
               if mcp.get("enabled") else " · MCP desligado")  # fmt: skip
    ready = f" · prontidão GET /{m['id']}/_hub/ready exigida no candidato (sem a rota: recusado)"
    jobs_txt = (" · tarefas: " + ", ".join(f"{j['name']} ({j['max_duration_s']} s, {j['max_attempts']}×)" for j in jobs)
                if jobs else "")  # fmt: skip
    stack = (
        f" · stack {m['runtime']['stack']}"
        if isinstance(m.get("runtime"), dict) and m["runtime"].get("stack")
        else ""
    )
    src = "imagem construída no hub a partir de build/" if is_build(image) else "imagem por digest"
    det = f"{src} · modo {mode} · entrada {m['routes'][0]}{stack}{jobs_txt}{api_txt}{mcp_txt}{ready}"
    return ("warn" if warn else "ok"), det, image


def _frontend(m: dict[str, Any], files: dict[str, bytes]) -> tuple[str, str, list[dict[str, str]]]:
    declared = {s["path"]: s for s in m.get("scripts") or []}
    html = css = 0
    for n, b in files.items():
        if n.startswith("build/"):
            continue  # contexto de build: não é front-end
        in_ui = n.startswith("ui/")
        if not in_ui and n not in ROOT_FILES:
            return "failed", f"arquivo fora de ui/: {n}", []
        low = n.lower()
        if low.endswith((".css", ".js")) and not in_ui:
            return "failed", f"CSS/JS fora de ui/: {n}", []
        css += low.endswith(".css")
        if low.endswith((".html", ".htm")):
            html += 1
            t = b.decode(errors="replace")
            if _inline_script(t):
                return "failed", f"script inline em {n} (incompatível com a CSP)", []
            if _inline_handler(t):
                return "failed", (f"atributo de evento inline (on…=) em {n}: incompatível com a CSP — "
                                  "use os atributos data-hub-* do hub"), []  # fmt: skip
            if "javascript:" in t.lower():
                return "failed", f"URL javascript: em {n}", []
    for n in files:
        if n.lower().endswith((".js", ".mjs")) and n not in declared and not n.startswith("build/"):
            return "failed", (f"JavaScript não declarado: {n} — apps não trazem JavaScript (use os atributos "
                              "data-hub-*); exceções vão em manifest.scripts com o motivo e passam por revisão"), []  # fmt: skip
    reviews: list[dict[str, str]] = []
    for p, s in declared.items():
        if p not in files:
            return "failed", f"manifest.scripts declara {p} mas o arquivo não está no pacote", []
        reviews.append({"path": p, "reason": s["reason"].strip(), "sha384": sri_sha384(files[p])})
    base = f"{sum(n.startswith('ui/') for n in files)} arquivo(s) em ui/ · {html} HTML · {css} CSS"
    if not reviews:
        return "ok", f"{base} · sem JavaScript próprio", []
    lst = "; ".join(f"{r['path']} ({r['sha384']}) — motivo: {r['reason']}" for r in reviews)
    return "warn", (f"{base} · REVISE: {SCRIPTS_CHECK} — exige aprovação explícita da administração (revisão de "
                    f"segurança, separada da aprovação da implantação; hash novo = nova aprovação): {lst}"), reviews  # fmt: skip
