"""`hub pack`: builds the .hubapp (manifest, runtime.json, openapi.json, ui/**, checksums.txt)."""

import hashlib
import io
import json
import zipfile
from pathlib import Path


class PackError(Exception):
    pass


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hub",
}


def _secretish(p: Path) -> bool:
    """Local state and secrets never go into the package (.env, deploy.*.env, old packages)."""
    n = p.name
    return (
        n == ".env"
        or n.startswith(".env.")  # .env.example too: the hub pipeline rejects any .env* in the package
        or n.endswith((".env", ".hubapp"))
        or n == ".coverage"
    )


def _context(root: Path) -> list[tuple[str, bytes]]:
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.is_symlink() and not (SKIP_DIRS & set(p.relative_to(root).parts))
        and p.suffix != ".pyc" and not _secretish(p)
    )  # fmt: skip
    return [("build/" + p.relative_to(root).as_posix(), p.read_bytes()) for p in files]


MAX_PACKAGE_BYTES = 20 * 1024 * 1024  # = hub/ops/pipeline.py (a test requires equality)


def pack(dir_: Path, image: str | None, out: Path, build_dir: Path | None = None,  # noqa: C901, PLR0912, PLR0915
         dockerfile: str = "Dockerfile") -> str:  # fmt: skip
    """Writes the package to `out` and returns the file's sha256. With `build_dir` the package carries the build context
    (`build/**`) and the hub builds the image; without it, `image` by digest is required."""
    mf = dir_ / "manifest.json"
    if not mf.is_file():
        raise PackError("manifest.json não encontrado")
    raw = mf.read_bytes()
    try:
        m = json.loads(raw)
    except ValueError as e:
        raise PackError(f"manifest.json inválido: {e}") from e
    if not isinstance(m, dict) or not m.get("id") or not m.get("version"):
        raise PackError("manifest.json sem id/version")
    if build_dir is not None:
        if not (build_dir / dockerfile).is_file():
            raise PackError(f"{dockerfile} não encontrado em {build_dir}")
        runtime: dict[str, object] = {"build": {"dockerfile": dockerfile}}
    elif image and "@sha256:" in image:
        runtime = {"image": image}
    else:
        raise PackError("imagem deve ser referenciada por digest (…@sha256:…) ou use --build-dir")
    api = m.get("api") or {}
    oa = dir_ / "openapi.json"
    entries: list[tuple[str, bytes]] = [
        ("manifest.json", raw),
        ("runtime.json", json.dumps(runtime, indent=2).encode()),
    ]
    if oa.is_file():
        entries.append(("openapi.json", oa.read_bytes()))
    elif api.get("enabled"):
        raise PackError(
            f"openapi.json não encontrado em {dir_}: a API é obrigatória (manifesto api.enabled = true). "
            f"Gere o documento do app (ex.: GET /{m['id']}{api.get('openapi', '/api/openapi.json')} com o "
            "app rodando, ou o comando de exportação do SDK) e salve ao lado do manifest.json"
        )
    ui = dir_ / "ui"
    if ui.is_dir():
        files = sorted(p.relative_to(dir_).as_posix() for p in ui.rglob("*") if p.is_file())
        entries += [(n, (dir_ / n).read_bytes()) for n in files]
    if build_dir is not None:
        entries += _context(build_dir)
    entries.append(("checksums.txt", "".join(f"{_sha(b)}  {n}\n" for n, b in entries).encode()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in entries:
            z.writestr(zipfile.ZipInfo(n, (1980, 1, 1, 0, 0, 0)), b, zipfile.ZIP_DEFLATED)
    data = buf.getvalue()
    if len(data) > MAX_PACKAGE_BYTES:  # the hub would refuse it with 400; say here what is too heavy
        big = sorted(((len(b), n) for n, b in entries), reverse=True)[:3]
        raise PackError(
            f"pacote com {len(data) / 1024 / 1024:.1f} MB; o hub aceita até "
            f"{MAX_PACKAGE_BYTES // 1024 // 1024} MB. Maiores arquivos: "
            + ", ".join(f"{n} ({sz / 1024 / 1024:.1f} MB)" for sz, n in big)
            + ". As dependências se instalam no build, não viajam no pacote: não deixe .venv/ nem vendor/ "
            "em build/ (use .dockerignore)"
        )
    out.write_bytes(data)
    return _sha(data)
