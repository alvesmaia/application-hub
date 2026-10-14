"""F24: developer documentation portal (`/developers/*`).

Content = Markdown versioned in `platform/docs/dev/` (+ the contract `platform/docs/CONTRATO-APP.md`),
rendered on the server (Markdown HTML turned off: the text never injects markup). The CLI and SDK
references (`cli.md`, `sdk.md`) are generated from the CLI's argparse and the `hub_sdk` docstrings/signatures:
`uv run python -m hub.ops.devdocs` (a test requires the file in the repository to be up to date).
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from functools import cache
from html import escape, unescape
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
HUB_MARK = "@HUB@"


@dataclass(frozen=True)
class Doc:
    slug: str  # path under /developers ("" = Getting started)
    section: str  # comecar | cli | sdk | guias | contrato
    title: str
    file: str  # relativo a platform/docs
    raw: str  # name in /dev/docs/<raw>.md
    split: bool = False  # ` becomes its own page (as in Guides)
    part: str = ""  # `


GUIDES = (
    Doc("guias/primeiro-app", "guias", "Primeiro app", "dev/guias/primeiro-app.md", "guia-primeiro-app"),
    Doc(
        "guias/publicar", "guias", "Publicar em staging e produção", "dev/guias/publicar.md", "guia-publicar"
    ),
    Doc("guias/banco", "guias", "Banco: SQLite e Postgres", "dev/guias/banco.md", "guia-banco"),
    Doc(
        "guias/armazenamento",
        "guias",
        "Armazenamento de arquivos",
        "dev/guias/armazenamento.md",
        "guia-armazenamento",
    ),
    Doc("guias/telas", "guias", "Padrões de tela (data-hub-*)", "dev/guias/telas.md", "guia-telas"),
    Doc(
        "guias/tempo-real",
        "guias",
        "Tempo real (SSE e WebSocket)",
        "dev/guias/tempo-real.md",
        "guia-tempo-real",
    ),
    Doc("guias/mcp-api", "guias", "API e MCP do app", "dev/guias/mcp-api.md", "guia-mcp-api"),
    Doc("guias/dashboards", "guias", "Dashboards", "dev/guias/dashboards.md", "guia-dashboards"),
)
DOCS = (
    Doc("", "comecar", "Começar", "dev/comecar.md", "comecar"),
    Doc("agente", "comecar", "Instruções para agentes", "dev/agente.md", "agente"),
    Doc("cli", "cli", "CLI", "dev/cli.md", "cli", split=True),
    Doc("sdk", "sdk", "SDK", "dev/sdk.md", "sdk", split=True),
    *GUIDES,
    Doc("contrato", "contrato", "Contrato do app", "CONTRATO-APP.md", "contrato", split=True),
)
SECTIONS = (("comecar", "Começar", ""), ("cli", "CLI", "cli"), ("sdk", "SDK", "sdk"),
            ("guias", "Guias", "guias"), ("contrato", "Contrato", "contrato"))  # fmt: skip


def docs_root() -> Path:
    return Path(os.getenv("HUB_PLATFORM_DIR", _REPO / "platform")) / "docs"


def _read(doc: Doc) -> str | None:
    p = docs_root() / doc.file
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Cuts the Markdown at the `## ` headings (outside code blocks): (introduction, [(title, body)])."""
    intro: list[str] = []
    parts: list[tuple[str, list[str]]] = []
    fence = False
    for line in text.splitlines():
        if line.startswith(("```", "~~~")):
            fence = not fence
        if not fence and line.startswith("## "):
            parts.append((line[3:].strip(), []))
        elif parts:
            # the section becomes a page: `###` moves up to `##` and so on
            parts[-1][1].append(line[1:] if not fence and re.match(r"#{3,6} ", line) else line)
        else:
            intro.append(line)
    return "\n".join(intro), [(t, "\n".join(b)) for t, b in parts]


def _part_slug(title: str) -> str:
    t = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", "", title)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:48].rstrip("-") or "secao"


# readable names of the pages derived from the CLI and the SDK (the technical name appears just below the title)
PART_TITLES = {
    "hub project create": "Criar um app", "hub project stacks": "Listar stacks", "hub check": "Validar o app",
    "hub pack": "Empacotar", "hub dev": "Rodar localmente", "hub login": "Entrar no hub",
    "hub deploy": "Publicar", "hub mcp": "Servidor MCP local",
    "hub_sdk": "Pacote principal", "hub_sdk.api": "API do app (OpenAPI)", "hub_sdk.app": "Ligar o SDK ao app",
    "hub_sdk.approvals": "Aprovações", "hub_sdk.dashboards": "Dashboards", "hub_sdk.db": "Banco de dados",
    "hub_sdk.flags": "Feature flags", "hub_sdk.heavy": "Operações pesadas", "hub_sdk.idempotency": "Idempotência",
    "hub_sdk.identity": "Identidade do usuário", "hub_sdk.jobs": "Tarefas duráveis",
    "hub_sdk.locale": "Idioma e formatação", "hub_sdk.mcp": "MCP do app", "hub_sdk.outbox": "Auditoria (outbox)",
    "hub_sdk.ready": "Prontidão", "hub_sdk.render": "Renderizar telas", "hub_sdk.saga": "Sagas",
    "hub_sdk.schema": "Tabelas do SDK", "hub_sdk.scripts": "JavaScript próprio", "hub_sdk.search": "Busca",
    "hub_sdk.shutdown": "Desligamento gracioso", "hub_sdk.sql": "Consultas SQL a conexões",
    "hub_sdk.stack": "Stack do app", "hub_sdk.storage": "Armazenamento de arquivos",
    "hub_sdk.telemetry": "Telemetria", "hub_sdk.uploads": "Envio de arquivos em duas fases", "hub_sdk.dashboards_format": "Dashboards: formatação", "hub_sdk.jobs_model": "Tarefas: modelo", "hub_sdk.jobs_store": "Tarefas: fila no banco", "hub_sdk.jobs_worker": "Tarefas: execução", "hub_sdk.storage_core": "Armazenamento: caminhos e erros", "hub_sdk.storage_s3": "Armazenamento: S3",
}  # fmt: skip


def _tech(part: str) -> str:
    return part.replace("`", "")


def _gist(body: str, limit: int = 140) -> str:
    """First sentence of the section's running text (no table, code, heading or list), for the index."""
    for para in re.split(r"\n\s*\n", body):
        t = " ".join(para.split())
        if t and not t.startswith(("|", "```", "#", "<", "-", "*", ">")) and not t[0].isdigit():
            t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
            t = str(re.split(r"(?<=\.)\s", t, maxsplit=1)[0])  # primeira frase
            if t.endswith(":"):  # only opens a list: summarizes nothing
                return ""
            return " — " + (t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…")
    return ""


def _tech_note(d: Doc) -> str:
    return f" · `{_tech(d.part)}`" if _tech(d.part) in PART_TITLES else ""


def all_docs() -> tuple[Doc, ...]:
    """DOCS with the pages derived from the `split` files right after the parent."""
    out: list[Doc] = []
    for d in DOCS:
        out.append(d)
        text = _read(d) if d.split else None
        for title, _ in _split(text)[1] if text else []:
            # no history notes in the title
            clean = PART_TITLES.get(_tech(title)) or re.sub(r"\s*\(onda [^)]*\)| — .*", "", _tech(title))
            out.append(Doc(f"{d.slug}/{_part_slug(title)}", d.section, clean, d.file, d.raw, part=title))
    return tuple(out)


def by_slug(slug: str) -> Doc | None:
    return next((d for d in all_docs() if d.slug == slug), None)


def by_raw(raw: str) -> Doc | None:
    return next((d for d in DOCS if d.raw == raw), None)


def source(doc: Doc, base: str) -> str | None:
    text = _read(doc)
    if text is None:
        return None
    if doc.part:
        tech = f"\n`{_tech(doc.part)}`\n" if _tech(doc.part) in PART_TITLES else ""
        text = next((f"# {doc.title}\n{tech}{body}" for t, body in _split(text)[1] if t == doc.part), None)
        if text is None:
            return None
    elif doc.split:  # parent page: introduction + index of the pages, with each one's summary
        kids = [d for d in all_docs() if d.part and d.file == doc.file]
        intro, parts = _split(text)
        bodies = dict(parts)
        items = (
            f"- [{d.title}](/developers/{d.slug}){_tech_note(d)}{_gist(bodies.get(d.part, ''))}" for d in kids
        )
        full = f"Texto completo numa página só (bom para agentes): [{doc.raw}.md](/dev/docs/{doc.raw}.md)"
        text = f"{intro}\n\n" + "\n".join(items) + f"\n\n{full}\n"
    return text.replace(HUB_MARK, base)


DATES = "dev/dates.json"  # {file: YYYY-MM-DD of the last commit}; `python -m hub.ops.devdocs --dates`


@cache
def _dates() -> dict[str, str]:
    import json

    p = docs_root() / DATES
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return {str(k): str(v) for k, v in data.items()}


def updated(doc: Doc) -> str:
    """Date of the source file's last commit (dates.json); without it, the file's date."""
    import datetime as dt

    if d := _dates().get(doc.file):
        return d
    p = docs_root() / doc.file
    return dt.date.fromtimestamp(p.stat().st_mtime).isoformat() if p.is_file() else ""


def git_dates() -> dict[str, str]:
    import subprocess

    root = _REPO / "platform" / "docs"
    out: dict[str, str] = {}
    for d in DOCS:
        r = subprocess.run(  # noqa: S603 - git fixo, argumentos internos
            ["git", "log", "-1", "--format=%cs", "--", d.file],  # noqa: S607
            cwd=root, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if r.stdout.strip():
            out[d.file] = r.stdout.strip()
    return out


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U).strip()
    return re.sub(r"[\s_]+", "-", s)[:64] or "secao"


_LEXERS = {"bash": "bash", "sh": "bash", "shell": "bash", "console": "bash", "python": "python", "py": "python",
           "json": "json", "toml": "toml", "yaml": "yaml", "yml": "yaml", "html": "html", "jinja": "html+jinja",
           "sql": "sql", "js": "javascript", "javascript": "javascript", "ts": "typescript",
           "typescript": "typescript"}  # fmt: skip


def _bash_pass(tokens: Any) -> Any:
    """Pygments marks few tokens in Bash: the 1st word of each command = function, `-f`/`--flag` = option,
    `\\` at the end of the line = operator."""
    from pygments.token import Comment, Keyword, Name, Operator, Punctuation, String, Text

    cmd = True
    for tok, val in tokens:
        if tok is String.Escape and val.startswith("\\") and val.endswith("\n"):
            yield Operator, val
            continue
        if tok in Keyword or (tok in (Operator, Punctuation) and val.strip() == "$("):
            # `if`/`then`/`do`/`$(`: the next word is a command; `for x in a b`: not
            cmd = val.strip() in ("if", "then", "else", "elif", "do", "while", "until", "$(", "!")
            yield tok, val
            continue
        if tok in (Text.Whitespace, Text) and not val.strip():
            cmd = cmd or "\n" in val
        elif tok in (Text, Name.Builtin) and cmd:
            tok, cmd = Name.Function, False
        elif tok is Text and val.startswith("-"):
            tok = Name.Attribute
        elif (
            tok in (Operator, Punctuation) and val.strip() in ("&&", "||", "|", ";", "(")
        ) or tok in Comment:
            cmd = True
        else:
            cmd = False
        yield tok, val


def _guess(code: str) -> str:
    """Block without a language: infers from the 1st line (every code box comes out colored); the rest becomes a command."""
    first = code.lstrip()
    if first.startswith(("{", "[")):
        return "json"
    if first.startswith(("<", "{%", "{{")):
        return "html"
    if re.match(r"(async def|def|class|from|import|@)\b|@", first):
        return "python"
    return "bash"


def highlight(code: str, lang: str) -> str:
    """Server-side highlighting (Pygments → `<span class="hl-*">`); a language outside the list = plain text."""
    name = _LEXERS.get(lang) if lang else _guess(code)
    if not name:
        return escape(code)
    from pygments import format as pyfmt
    from pygments.formatters.html import HtmlFormatter
    from pygments.lexers import get_lexer_by_name

    tokens = get_lexer_by_name(name).get_tokens(code)
    if name == "bash":
        tokens = _bash_pass(tokens)
    return str(pyfmt(tokens, HtmlFormatter(nowrap=True, classprefix="hl-")))


@cache
def _md() -> Any:
    from markdown_it import MarkdownIt  # lazy import: only the documentation pages need it

    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

    def fence(_self: Any, tokens: list[Any], idx: int, _opts: Any, env: Any) -> str:
        tok = tokens[idx]
        lang = tok.info.split()[0].lower() if tok.info.strip() else ""
        return (
            f'<div class="dev-code"><pre><code>{highlight(tok.content, lang)}</code></pre>'
            '<button type="button" class="dev-code-copy" data-dev-copy="">'
            f'<span data-dev-copy-idle="">{env.get("copy", "Copiar")}</span>'
            f'<span data-dev-copy-done="" hidden="">{env.get("copied", "Copiado")}</span></button></div>\n'
        )

    md.add_render_rule("fence", fence)
    md.add_render_rule("code_block", fence)
    return md


def render_md(
    text: str, labels: tuple[str, str] = ("Copiar", "Copiado")
) -> tuple[str, list[tuple[int, str, str]]]:
    """Markdown → HTML (anchors on headings, copy button on blocks, scrollable tables) + summary."""
    md = _md()
    tokens = md.parse(re.sub(r"(?m)^<!--.*-->\n", "", text))  # the generation comment does not appear
    seen: dict[str, int] = {}
    toc: list[tuple[int, str, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            title = tokens[i + 1].content
            base = _slugify(re.sub(r"`", "", title))
            n = seen.get(base, 0)
            seen[base] = n + 1
            hid = base if n == 0 else f"{base}-{n}"
            tok.attrSet("id", hid)
            if tok.tag in ("h2", "h3"):  # the Markdown h1 is the page title
                toc.append((int(tok.tag[1]), re.sub(r"`", "", title), hid))
    html = str(
        md.renderer.render(tokens, md.options, {"copy": escape(labels[0]), "copied": escape(labels[1])})
    )
    html = html.replace("<table>", '<div class="dev-table"><table class="data-table">').replace(
        "</table>", "</table></div>"
    )

    # heading that is only a signature (`f(x: int) -> str`, `class X`): colored as Python
    def sig(m: re.Match[str]) -> str:
        code = highlight(unescape((m[2] or "") + m[3]), "python")
        if m[2] != "class ":  # the function name gets the function color
            code = code.replace('class="hl-n"', 'class="hl-nf"', 1)
        return f'{m[1]}<code class="dev-sig">{code}</code>{m[4]}'

    html = re.sub(
        r'(<h[2-4] id="[^"]*">)(class |async )?<code>((?:async |class )?[\w.]+(?:\(.*?)?)</code>(</h[2-4]>)',
        sig,
        html,
    )
    return html, toc


# ---- prompt «Copiar prompt» ----------------------------------------------------------------------

AGENTS = (("claude", "Claude Code"), ("codex", "OpenAI Codex"), ("copilot", "GitHub Copilot"),
          ("cursor", "Cursor"), ("opencode", "OpenCode"))  # fmt: skip

_PROMPTS = {
    "pt-BR": "Siga as instruções em {base}/dev/docs/agente.md para criar e publicar um app neste Application Hub.",
    "en": "Follow the instructions at {base}/dev/docs/agente.md to create and publish an app on this Application Hub.",
}


def prompt(lang: str, base: str) -> str:
    return _PROMPTS.get(lang, _PROMPTS["pt-BR"]).format(base=base)


# ---- generation of the references (CLI and SDK) -----------------------------------------------------------

_GEN = "<!-- generated by `uv run python -m hub.ops.devdocs` — do not edit by hand -->\n"


def _sub_parsers(p: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser, str]]:
    out: list[tuple[str, argparse.ArgumentParser, str]] = []
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            helps = {c.dest: c.help or "" for c in a._choices_actions}
            out += [(n, sp, helps.get(n, "")) for n, sp in a.choices.items()]
    return out


def _cell(s: str) -> str:
    return " ".join(s.split()).replace("|", "\\|")


def cli_reference() -> str:  # noqa: C901, PLR0915
    cli = _REPO / "platform-py" / "cli"
    if str(cli) not in sys.path:
        sys.path.insert(0, str(cli))
    for v in ("HUB_URL", "HUB_TOKEN_FILE", "HUB_MCP_PATH"):
        os.environ.pop(v, None)
    from hub_cli import errors
    from hub_cli import main as cli_main

    root = cli_main.parser()
    lines = [_GEN, "# CLI reference", "",
             "Generated from the `--help` of the `hub` CLI (argparse). Run `hub <command> --help` for the same information in the "
             "terminal.", "", "## Exit codes", "", "| Code | Meaning |", "|---|---|"]  # fmt: skip
    meaning = {errors.OK: "ok", errors.FAIL: "failure (check or step)",
               errors.VALIDATION: "validation (missing or invalid argument)", errors.AUTH: "authentication",
               errors.UNREACHABLE: "hub unreachable"}  # fmt: skip
    lines += [f"| `{k}` | {v} |" for k, v in sorted(meaning.items())] + [""]

    def walk(prefix: str, p: argparse.ArgumentParser, help_: str) -> None:
        subs = _sub_parsers(p)
        if subs:
            for n, sp, h in subs:
                walk(f"{prefix} {n}", sp, h)
            return
        lines.extend([f"## `{prefix}`", ""])
        if help_:
            lines.extend([help_.rstrip(".") + ".", ""])
        usage = " ".join(p.format_usage().replace("usage:", "").split())
        lines.extend(["```", usage, "```", ""])
        args = [a for a in p._actions if not isinstance(a, argparse._HelpAction)]
        if args:
            lines.extend(["| Argument | Description | Default |", "|---|---|---|"])
            for a in args:
                name = ", ".join(f"`{s}`" for s in a.option_strings) or f"`{a.dest}`"
                if a.choices:
                    name += " (" + " \\| ".join(str(c) for c in a.choices) + ")"
                d = a.default
                dflt = "" if d in (None, False, argparse.SUPPRESS) or a.required else f"`{d}`"
                req = " (required)" if a.required else ""
                lines.append(f"| {name} | {_cell(a.help or '')}{req} | {dflt} |")
            lines.append("")
        ep = p.epilog or ""
        if "exemplo" in ep:
            ex = ep[ep.index("exemplo") :].split("\n", 1)[1]
            lines.extend(["Exemplos:", "", "```", ex.strip("\n"), "```", ""])

    walk("hub", root, "")
    return "\n".join(lines).rstrip() + "\n"


_SDK_SKIP = {"e2e_hostile", "internal"}


def _first(doc: str | None) -> str:
    return (doc or "").strip().split("\n\n")[0].strip()


def _sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    a = fn.args
    if a.args and a.args[0].arg in ("self", "cls"):
        a = ast.arguments(posonlyargs=a.posonlyargs, args=a.args[1:], vararg=a.vararg, kwonlyargs=a.kwonlyargs,
                          kw_defaults=a.kw_defaults, kwarg=a.kwarg, defaults=a.defaults)  # fmt: skip
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    pre = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    return f"{pre}{fn.name}({ast.unparse(a)}){ret}"


def sdk_reference() -> str:
    src = _REPO / "platform" / "sdk-python" / "src" / "hub_sdk"
    lines = [_GEN, "# SDK reference (`hub_sdk`)", "",
             "Generated from the signatures and docstrings of the `hub_sdk` package. Public names only (no `_`).", ""]  # fmt: skip
    for f in sorted(src.glob("*.py")):
        mod = f.stem
        if mod in _SDK_SKIP or (mod.startswith("_") and mod != "__init__"):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        name = "hub_sdk" if mod == "__init__" else f"hub_sdk.{mod}"
        items: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                items += [f"### `{_sig(node)}`", "", _first(ast.get_docstring(node)), ""]
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                items += [f"### class `{node.name}`", "", _first(ast.get_docstring(node)), ""]
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_"):
                        d = _first(ast.get_docstring(m))
                        items.append(f"- `{_sig(m)}`" + (f" — {' '.join(d.split())}" if d else ""))
                if items[-1].startswith("- "):
                    items.append("")
        doc = _first(ast.get_docstring(tree))
        if not items and not doc:
            continue
        lines += [f"## `{name}`", ""] + ([doc, ""] if doc else []) + items
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"


def generated() -> dict[Path, str]:
    d = _REPO / "platform" / "docs" / "dev"
    return {d / "cli.md": cli_reference(), d / "sdk.md": sdk_reference()}


def main() -> int:  # pragma: no cover - command-line tool
    if "--dates" in sys.argv:
        import json

        p = _REPO / "platform" / "docs" / DATES
        p.write_text(json.dumps(git_dates(), indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"escrito {p}")
        return 0
    for p, text in generated().items():
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"escrito {p}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
