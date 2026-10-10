"""`hub project create|stacks`: novo app a partir de um template embutido (depois: catálogo do hub)."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .errors import CliError

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
RESERVED = {"apps", "hub", "internal", "platform", "postgres", "template0", "template1"}
REQUIRES = ("database", "storage")
# Arquivos sem ponto no template (o empacotador ignoraria) → nome final.
RENAMES = {"gitignore": ".gitignore", "dockerignore": ".dockerignore"}


@dataclass(frozen=True)
class Stack:
    id: str
    name: str
    description: str


STACKS = (
    Stack(
        "python-fastapi",
        "Python + FastAPI",
        "FastAPI, Jinja2 (SSR), hub_sdk, SQLAlchemy Core + Alembic (SQLite no dev, Postgres no hub), "
        "ruff, ty, pytest",
    ),
)


def stacks() -> list[dict[str, str]]:
    return [{"id": s.id, "name": s.name, "description": s.description} for s in STACKS]


def validate_id(app_id: str) -> str:
    if (
        not ID_RE.match(app_id)
        or app_id in RESERVED
        or "--" in app_id
        or app_id.endswith("-")
        or app_id.startswith(("hub-", "pg-"))
        or app_id.endswith(("-next", "-prev", "-swap", "-staging"))
    ):
        raise CliError(
            f"id inválido: {app_id!r} (2–32 caracteres a-z, 0-9 e '-', começando por letra; não reservado)",
            2,
        )
    return app_id


def parse_requires(raw: str | None) -> dict[str, object]:
    want = [p.strip() for p in (raw or "database").split(",") if p.strip()]
    bad = [p for p in want if p not in REQUIRES]
    if bad:
        raise CliError(f"--requires: valor desconhecido {', '.join(bad)} (use {', '.join(REQUIRES)})", 2)
    return {"database": "database" in want, "storage": "storage" in want, "variables": [], "secrets": []}


TEMPLATES = ("basic", "dashboard")


def parse_template(template: str | None, with_: str | None) -> str:
    extras = [p.strip() for p in (with_ or "").split(",") if p.strip()]
    bad = [p for p in extras if p != "dashboards"]
    if bad:
        raise CliError(f"--with: valor desconhecido {', '.join(bad)} (use dashboards)", 2)
    if template and template not in TEMPLATES:
        raise CliError(f"--template: use {' ou '.join(TEMPLATES)}", 2)
    return "dashboard" if extras or template == "dashboard" else "basic"


def sdk_wheel() -> Path:
    whl = sorted((Path(str(resources.files("hub_cli"))) / "_wheels").glob("hub_sdk-*.whl"))
    if not whl:
        raise CliError("SDK não embutido nesta CLI (reinstale a hub-cli do hub)", 1)
    return whl[-1]


def create(
    app_id: str,
    name: str,
    stack: str,
    db: str,
    requires: dict[str, object],
    directory: Path,
    template: str = "basic",
) -> dict[str, object]:
    """Copia o template da stack; ``template="dashboard"`` sobrepõe ``<stack>.dashboard`` (arquivos do kit de
    dashboards: Painel com KPIs, gráficos, tabela, filtro e exportação + dados de exemplo para o `hub dev`)."""
    validate_id(app_id)
    if not name.strip() or len(name) > 80:
        raise CliError("--name: informe 1–80 caracteres", 2)
    if stack not in {s.id for s in STACKS}:
        raise CliError(f"--stack: {stack!r} desconhecida (veja `hub project stacks`)", 2)
    if db != "sqlite" and not re.match(r"^(postgres(ql)?(\+\w+)?|sqlite)://", db):
        raise CliError("--db: use sqlite ou uma URL (postgresql://…)", 2)
    if directory.exists() and any(directory.iterdir()):
        raise CliError(f"pasta {directory} já existe e não está vazia", 2)
    pkg = app_id.replace("-", "_")
    wheel = sdk_wheel()
    subst = {
        "__APP_ID__": app_id,
        "__APP_NAME__": name.replace('"', "'"),
        "__PKG__": pkg,
        "__SDK_WHEEL__": wheel.name,
        "__REQUIRES__": json.dumps(requires),
    }
    root = Path(str(resources.files("hub_cli"))) / "templates"
    layers = [root / stack] + ([root / f"{stack}.dashboard"] if template == "dashboard" else [])
    if template == "dashboard" and not layers[-1].is_dir():
        raise CliError(f"--template dashboard: indisponível para a stack {stack}", 2)
    files: list[str] = []
    skip = {"__pycache__", ".ruff_cache", ".pytest_cache"}
    for src in layers:
        for f in sorted(p for p in src.rglob("*") if p.is_file() and not skip & set(p.parts)):
            rel = f.relative_to(src).as_posix()
            for k, v in subst.items():
                rel = rel.replace(k, v)
            rel = RENAMES.get(rel, rel)
            out = directory / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text(encoding="utf-8")
            for k, v in subst.items():
                text = text.replace(k, v)
            out.write_text(text, encoding="utf-8", newline="\n")
            if rel not in files:
                files.append(rel)
    (directory / "vendor").mkdir(exist_ok=True)
    shutil.copy2(wheel, directory / "vendor" / wheel.name)
    files.append(f"vendor/{wheel.name}")
    if db != "sqlite":
        (directory / ".env").write_text(f"DATABASE_URL={db}\n", encoding="utf-8")
    return {"id": app_id, "name": name, "stack": stack, "template": template, "dir": str(directory.resolve()), "files": files,
            "requires": requires, "next": ["uv sync", "hub check", "hub dev"]}  # fmt: skip
