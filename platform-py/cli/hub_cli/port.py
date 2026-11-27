"""Adapting an existing app (`port.json` at the project root): what `hub check` holds the port to.

- **The original CSS stays untouched.** Each original stylesheet is copied byte for byte (`css`: pairs
  `[path in the original, path in the project]`, in cascade order); every change the hub needs goes to one
  `overrides` file. `app_css` (what the app serves) must be exactly the copies followed by the overrides —
  `hub port css` writes it. A copy that differs from the original fails (when the original folder is at hand).
- **The inventory is done.** `port-inventory.md` lists the original's routes and behaviors as a checklist:
  `- [x]` ported, `- [~] … — reason` a deviation (the reason is required), `- [ ]` still to do (fails).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from hub_cli.errors import CliError

CONFIG = "port.json"
INVENTORY = "port-inventory.md"
_ITEM = re.compile(r"^\s*- \[(.)\]\s*(.*)$")


@dataclass
class Port:
    original: Path
    css: list[tuple[str, str]]
    app_css: str
    overrides: str


def load(directory: Path) -> Port | None:
    """The project's `port.json`, or None when the project is not a port."""
    f = directory / CONFIG
    if not f.is_file():
        return None
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        pairs = [(str(a), str(b)) for a, b in raw.get("css", [])]
        return Port(Path(raw["original"]), pairs, str(raw["app_css"]), str(raw["overrides"]))
    except (ValueError, KeyError, TypeError) as e:
        raise CliError(
            f"{CONFIG} inválido ({e}): esperado "
            '{"original": "<pasta do app original>", "css": [["<no original>", "<no projeto>"]], '
            '"app_css": "<css servido>", "overrides": "<ajustes do hub>"}',
            2,
        ) from e


def built_css(directory: Path, port: Port) -> str:
    """The copies in cascade order, then the overrides: what `app_css` must hold."""
    parts = [(directory / dst).read_text(encoding="utf-8") for _, dst in port.css]
    over = directory / port.overrides
    parts.append(over.read_text(encoding="utf-8") if over.is_file() else "")
    return "\n".join(parts)


def css_problems(directory: Path, port: Port) -> list[str]:
    original = port.original if port.original.is_absolute() else directory / port.original
    out: list[str] = []
    for src, dst in port.css:
        copy = directory / dst
        if not copy.is_file():
            out.append(f"cópia ausente: {dst} (de {src})")
        elif (original / src).is_file() and (original / src).read_bytes() != copy.read_bytes():
            out.append(f"{dst} difere do original {src}: ajustes vão em {port.overrides}")
    if not out and (directory / port.app_css).read_text(encoding="utf-8") != built_css(directory, port):
        out.append(f"{port.app_css} não é cópias + {port.overrides}: rode `hub port css`")
    return out


def inventory_problems(directory: Path) -> list[str]:
    f = directory / INVENTORY
    if not f.is_file():
        return [f"{INVENTORY} ausente: inventarie rotas e comportamentos do original (AGENTS.md, passo 1)"]
    items = [m.groups() for ln in f.read_text(encoding="utf-8").splitlines() if (m := _ITEM.match(ln))]
    if not items:
        return [f"{INVENTORY} sem itens (`- [x] …`)"]
    todo = [f"pendente: {text}" for mark, text in items if mark == " "]
    loose = [
        f"desvio sem motivo (`— motivo`): {text}" for mark, text in items if mark == "~" and " — " not in text
    ]
    return todo + loose


def step_port(directory: Path) -> tuple[bool, str]:
    port = load(directory)
    if port is None:
        return True, f"sem {CONFIG}: não é adequação de app existente"
    problems = css_problems(directory, port) + inventory_problems(directory)
    return not problems, "\n".join(problems) or "CSS original intacto; inventário concluído"


def write_css(directory: Path) -> Path:
    """`hub port css`: writes `app_css` from the copies and the overrides."""
    port = load(directory)
    if port is None:
        raise CliError(f"{CONFIG} não encontrado em {directory.resolve()}", 2)
    out = directory / port.app_css
    out.write_text(built_css(directory, port), encoding="utf-8")
    return out
