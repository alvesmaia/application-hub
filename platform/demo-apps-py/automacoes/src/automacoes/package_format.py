"""Package format: a zip with ``automation.json`` at the root and pure-Python files.

``automation.json``::

    {"name": "consultar-cep", "version": "1.0.0", "description": "...",
     "entrypoint": "main.py:run", "fields": [{"name": "cep", "label": "CEP", "type": "text", ...}],
     "labels": {"uf": "UF"}}

:func:`inspect_zip` checks size, file count, paths (no absolute path, ``..`` or symlink — zip-slip),
the manifest schema (errors prefixed with their path, e.g. ``fields.0.type: …``) and that the
entrypoint file exists. :func:`extract` writes a checked package into a directory.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from automacoes.fields import FieldSpec

MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_FILES = 200
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MANIFEST = "automation.json"


class PackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
    description: str = Field(default="", max_length=500)
    entrypoint: str = Field(pattern=r"^[A-Za-z_][\w/]*\.py:[A-Za-z_]\w*$")
    fields: list[FieldSpec] = Field(default_factory=list, max_length=50)
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_fields(self) -> PackageManifest:
        names = [f.name for f in self.fields]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate field names {dup}; each field needs its own name")
        return self

    @property
    def entry_file(self) -> str:
        return self.entrypoint.split(":", 1)[0]


@dataclass(frozen=True)
class Inspected:
    manifest: PackageManifest | None
    errors: list[str]
    sha256: str
    size: int


class PackageError(ValueError):
    """A package that cannot be stored; ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def inspect_zip(data: bytes) -> Inspected:
    digest = hashlib.sha256(data).hexdigest()
    errors = _size_errors(data)
    if errors:
        return Inspected(None, errors, digest, len(data))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest, errors = _check_archive(zf)
    except zipfile.BadZipFile:
        return Inspected(None, ["arquivo: não é um zip válido"], digest, len(data))
    return Inspected(manifest if not errors else None, errors, digest, len(data))


def _size_errors(data: bytes) -> list[str]:
    if len(data) > MAX_ZIP_BYTES:
        return [f"arquivo: {len(data)} bytes; o limite é {MAX_ZIP_BYTES} bytes (5 MB)"]
    return [] if data else ["arquivo: vazio"]


def _check_archive(zf: zipfile.ZipFile) -> tuple[PackageManifest | None, list[str]]:
    infos = zf.infolist()
    errors = [e for i in infos if (e := path_error(i))]
    if len(infos) > MAX_FILES:
        errors.append(f"arquivo: {len(infos)} entradas; o limite é {MAX_FILES}")
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        errors.append(f"arquivo: mais de {MAX_UNPACKED_BYTES} bytes descompactados")
    if errors:
        return None, errors
    names = {i.filename for i in infos}
    if MANIFEST not in names:
        return None, [f"{MANIFEST}: ausente na raiz do zip"]
    manifest, errors = parse_manifest(zf.read(MANIFEST))
    if manifest is not None and manifest.entry_file not in names:
        errors.append(f"entrypoint: arquivo {manifest.entry_file!r} não está no pacote")
    return manifest, errors


def path_error(i: zipfile.ZipInfo) -> str:
    """Why an entry cannot be extracted safely (empty when it can)."""
    name = i.filename
    p = PurePosixPath(name.replace("\\", "/"))
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return f"{name}: caminho absoluto não é permitido"
    if ".." in p.parts:
        return f"{name}: '..' no caminho não é permitido"
    if stat.S_ISLNK(i.external_attr >> 16):
        return f"{name}: links simbólicos não são permitidos"
    return ""


def parse_manifest(raw: bytes) -> tuple[PackageManifest | None, list[str]]:
    try:
        doc: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, [f"{MANIFEST}: JSON inválido ({e})"]
    try:
        return PackageManifest.model_validate(doc), []
    except ValidationError as e:
        return None, [
            f"{'.'.join(str(x) for x in err['loc']) or MANIFEST}: {err['msg']}" for err in e.errors()
        ]


def extract(data: bytes, dest: Path) -> None:
    """Writes the package's files under ``dest`` (paths re-checked: never outside it)."""
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for i in zf.infolist():
            target = (root / i.filename).resolve()
            if path_error(i) or not target.is_relative_to(root):
                raise PackageError([path_error(i) or f"{i.filename}: fora do diretório do pacote"])
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(i))


def build_zip(folder: Path) -> bytes:
    """Zip of a folder (sample packages and tests), files in a stable order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(folder).as_posix())
    return buf.getvalue()
