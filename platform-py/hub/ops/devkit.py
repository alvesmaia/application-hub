"""F22: distribuição do kit do desenvolvedor pelo hub e a tela «Desenvolvedores».

- `/dev/cli/simple/` — índice PEP 503 (só leitura, sem sessão) com os wheels da `hub-cli` e do `hub-sdk` gerados na
  imagem (`scripts/build-cli.sh`): `uv tool install hub-cli --index <hub>/dev/cli/simple/`.
- `/dev/cli/files/<wheel>` — download direto do wheel (lista fechada: só os arquivos do diretório).
- `/dev/docs/*.md` — Markdown da documentação (público com `HUB_PUBLIC_DOCS=1`, o padrão; `0` exige login).
- `/developers` — instalação, comandos, stacks, links (token, contrato, validador de manifesto).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
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


def mount_devkit(app: FastAPI, render: Callable[..., Response], public_url: Callable[[Request], str]) -> None:
    @app.get("/dev/cli/simple/", response_class=HTMLResponse)
    async def simple_root() -> HTMLResponse:
        names = sorted({_project(p) for p in wheels()})
        links = "".join(f'<a href="{n}/">{n}</a>\n' for n in names)
        return HTMLResponse(f"<!doctype html><html><body>\n{links}</body></html>")

    @app.get("/dev/cli/simple/{project}/", response_class=HTMLResponse)
    async def simple_project(project: str) -> Response:
        files = [p for p in wheels() if _project(p) == project]
        if not files:
            return PlainTextResponse("não encontrado", status_code=404)
        links = "".join(f'<a href="../../files/{p.name}#sha256={_sha(p)}">{p.name}</a>\n' for p in files)
        return HTMLResponse(f"<!doctype html><html><body>\n{links}</body></html>")

    @app.get("/dev/cli/files/{name}")
    async def wheel_file(name: str) -> Response:
        match = next((p for p in wheels() if p.name == name), None)
        if match is None:
            return PlainTextResponse("não encontrado", status_code=404)
        return FileResponse(match, media_type="application/octet-stream", filename=match.name)

    @app.get("/dev/docs/{doc}.md")
    async def doc_file(request: Request, doc: str) -> Response:
        p = docs().get(doc)
        if p is not None and p.is_file():
            text: str | None = p.read_text(encoding="utf-8")
        else:
            d = devdocs.by_raw(doc)
            text = devdocs.source(d, public_url(request)) if d else None
        if text is None:
            return PlainTextResponse("não encontrado", status_code=404)
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")

    @app.get("/llms.txt")
    @app.get("/dev/llms.txt")  # sob /dev/*: mesmo acesso público das páginas atrás de proxy/Access
    async def llms(request: Request) -> Response:
        """Índice para agentes (llmstxt.org): o Markdown cru de cada página, com o código literal."""
        base = public_url(request)
        lines = ["# Application Hub — documentação para desenvolvedores", ""]
        lines += [
            "> Leia os arquivos .md abaixo: trazem o texto e o código completos (um resumo do HTML perde código).",
            "",
        ]
        lines += [f"- [{d.title}]({base}/dev/docs/{d.raw}.md)" for d in devdocs.all_docs() if not d.part]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")

    async def page(request: Request, slug: str) -> Response:
        doc = devdocs.by_slug(slug)
        base = public_url(request)
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
            # visitante (documentação pública): shell mínimo, sem menus
            "base_tpl": "base.html" if getattr(request.state, "user", None) else "public_base.html",
            "raw_url": f"/dev/docs/{doc.raw}.md",
            "prompt": devdocs.prompt(lang, base) if doc.slug == "" else "",
            "agents": devdocs.AGENTS,
            "updated": devdocs.updated(doc),
            "lang_note": lang != "pt-BR",
        }
        return render(request, "developers.html", "Aplicações", "Desenvolvedores",
                      "Documentação para criar, testar e publicar apps da plataforma.",
                      tab=doc.title, **ctx)  # fmt: skip

    @app.get("/developers", response_class=HTMLResponse)
    async def developers(request: Request) -> Response:
        return await page(request, "")

    @app.get("/developers/guias", response_class=HTMLResponse)
    async def developers_guides(request: Request) -> Response:
        return await page(request, devdocs.GUIDES[0].slug)

    @app.get("/developers/{slug:path}", response_class=HTMLResponse)
    async def developers_doc(request: Request, slug: str) -> Response:
        return await page(request, slug.strip("/"))
