"""F22: distribution of the developer kit by the hub, and the "Developers" screen.

- `/dev/cli/simple/` — PEP 503 index (read-only, no session) with the wheels of `hub-cli` and `hub-sdk` generated in
  imagem (`scripts/build-cli.sh`): `uv tool install hub-cli --index <hub>/dev/cli/simple/`.
- `/dev/cli/files/<wheel>` — direct wheel download (closed list: only the files in the directory).
- `/dev/docs/*.md` — documentation Markdown (public with `HUB_PUBLIC_DOCS=1`, the default; `0` requires login).
- `/developers` — installation, commands, stacks, links (token, contract, manifest validator).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from hub.ops import devdocs
from hub.ux.texts import tr

_REPO = Path(__file__).resolve().parents[3]
STACKS = (
    (
        "python-fastapi",
        "Python + FastAPI",
        "FastAPI, Jinja2, hub_sdk, SQLAlchemy Core + Alembic, ruff, ty, pytest",
    ),
)
_WHEEL = re.compile(r"^(hub_cli|hub_sdk)-[0-9][0-9A-Za-z.+-]*-py3-none-any\.whl$")
PUBLIC_PREFIX = "/dev/cli/"


def dist_dir() -> Path:
    env = os.getenv("HUB_CLI_DIST")
    if env:
        return Path(env)
    built = Path("/platform/dist/cli")
    return built if built.is_dir() else _REPO / "platform-py" / "dist" / "cli"


def docs() -> dict[str, Path]:
    root = Path(os.getenv("HUB_PLATFORM_DIR", _REPO / "platform"))
    agents = root / "docs" / "AGENTS-app.md"
    if not agents.is_file():
        agents = _REPO / "platform-py" / "cli" / "hub_cli" / "templates" / "python-fastapi" / "AGENTS.md"
    return {"agents": agents, "contrato": root / "docs" / "CONTRATO-APP.md"}


def wheels() -> list[Path]:
    d = dist_dir()
    return sorted(p for p in d.glob("*.whl") if _WHEEL.match(p.name)) if d.is_dir() else []


def _project(p: Path) -> str:
    return p.name.split("-", 1)[0].replace("_", "-")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@dataclass(frozen=True)
class _Kit:
    """What the pages need from the hub that mounts them (kept on `app.state.devkit`)."""

    render: Callable[..., Response]
    public_url: Callable[[Request], str]


def _kit(request: Request) -> _Kit:
    return cast(_Kit, request.app.state.devkit)


router = APIRouter()


def mount_devkit(app: FastAPI, render: Callable[..., Response], public_url: Callable[[Request], str]) -> None:
    app.state.devkit = _Kit(render, public_url)
    app.include_router(router)


@router.get("/dev/cli/simple/", response_class=HTMLResponse)
async def simple_root() -> HTMLResponse:
    names = sorted({_project(p) for p in wheels()})
    links = "".join(f'<a href="{n}/">{n}</a>\n' for n in names)
    return HTMLResponse(f"<!doctype html><html><body>\n{links}</body></html>")


@router.get("/dev/cli/simple/{project}/", response_class=HTMLResponse)
async def simple_project(project: str) -> Response:
    files = [p for p in wheels() if _project(p) == project]
    if not files:
        return PlainTextResponse("não encontrado", status_code=404)
    links = "".join(f'<a href="../../files/{p.name}#sha256={_sha(p)}">{p.name}</a>\n' for p in files)
    return HTMLResponse(f"<!doctype html><html><body>\n{links}</body></html>")


@router.get("/dev/cli/files/{name}")
async def wheel_file(name: str) -> Response:
    match = next((p for p in wheels() if p.name == name), None)
    if match is None:
        return PlainTextResponse("não encontrado", status_code=404)
    return FileResponse(match, media_type="application/octet-stream", filename=match.name)


@router.get("/dev/docs/{doc}.md")
async def doc_file(request: Request, doc: str) -> Response:
    p = docs().get(doc)
    if p is not None and p.is_file():
        text: str | None = p.read_text(encoding="utf-8")
    else:
        d = devdocs.by_raw(doc)
        text = devdocs.source(d, _kit(request).public_url(request)) if d else None
    if text is None:
        return PlainTextResponse("não encontrado", status_code=404)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/llms.txt")
@router.get("/dev/llms.txt")  # under /dev/*: same public access as the pages behind proxy/Access
async def llms(request: Request) -> Response:
    """Index for agents (llmstxt.org): the raw Markdown of each page, with the literal code."""
    base = _kit(request).public_url(request)
    lines = ["# Application Hub — documentação para desenvolvedores", ""]
    lines += [
        "> Leia os arquivos .md abaixo: trazem o texto e o código completos (um resumo do HTML perde código).",
        "",
    ]
    lines += [f"- [{d.title}]({base}/dev/docs/{d.raw}.md)" for d in devdocs.all_docs() if not d.part]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


async def _page(request: Request, slug: str) -> Response:
    doc = devdocs.by_slug(slug)
    base = _kit(request).public_url(request)
    text = devdocs.source(doc, base) if doc else None
    if doc is None or text is None:
        return PlainTextResponse("não encontrado", status_code=404)
    lang = getattr(request.state, "lang", "pt-BR")
    html, toc = devdocs.render_md(text, (tr("Copiar", lang), tr("Copiado", lang)))
    ctx: dict[str, Any] = {
        "doc": doc,
        "doc_html": html,
        "toc": toc,
        "sections": devdocs.SECTIONS,
        "docs": devdocs.all_docs(),
        # visitor (public documentation): minimal shell, no menus
        "base_tpl": "base.html" if getattr(request.state, "user", None) else "public_base.html",
        "raw_url": f"/dev/docs/{doc.raw}.md",
        "prompt": devdocs.prompt(lang, base) if doc.slug == "" else "",
        "agents": devdocs.AGENTS,
        "updated": devdocs.updated(doc),
        "lang_note": lang != "pt-BR",
    }
    return _kit(request).render(request, "developers.html", "Aplicações", "Desenvolvedores",
                                "Documentação para criar, testar e publicar apps da plataforma.",
                                tab=doc.title, **ctx)  # fmt: skip


@router.get("/developers", response_class=HTMLResponse)
async def developers(request: Request) -> Response:
    return await _page(request, "")


@router.get("/developers/guias", response_class=HTMLResponse)
async def developers_guides(request: Request) -> Response:
    return await _page(request, devdocs.GUIDES[0].slug)


@router.get("/developers/{slug:path}", response_class=HTMLResponse)
async def developers_doc(request: Request, slug: str) -> Response:
    return await _page(request, slug.strip("/"))
