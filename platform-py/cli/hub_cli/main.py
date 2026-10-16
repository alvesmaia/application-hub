"""Entry point for `hub` (project, dev, check, pack, mcp)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from . import check as check_mod
from . import deploy as deploy_mod
from . import dev as dev_mod
from . import mcp as mcp_mod
from . import project
from .errors import FAIL, VALIDATION, CliError
from .pack import PackError, pack

EXIT_CODES = """exit codes: 0 ok · 1 failure (check/step) · 2 validation (missing or invalid argument) ·
3 authentication · 4 hub unreachable"""

EPILOG = f"""{EXIT_CODES}

examples:
  hub project create estoque --stack python-fastapi --yes --json
  hub project stacks --json
  hub check
  hub dev
  hub pack --build-dir ."""


class Parser(argparse.ArgumentParser):
    """Usage error = exit code 2 (validation), with the examples hint."""

    @override
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(VALIDATION)


FMT = argparse.RawDescriptionHelpFormatter
if TYPE_CHECKING:  # argparse exposes no public name for this type
    Subparsers = argparse._SubParsersAction[Parser]


def parser() -> argparse.ArgumentParser:
    p = Parser(prog="hub", description="Application Hub CLI", epilog=EPILOG, formatter_class=FMT)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    for add in (add_project, add_check, add_pack, add_dev, add_login, add_deploy, add_mcp):
        add(sub)
    return p


def add_project(sub: Subparsers) -> None:
    fmt = FMT
    pr = sub.add_parser("project", help="Create apps from the templates", epilog=EPILOG, formatter_class=fmt)
    psub = pr.add_subparsers(dest="pcmd", required=True, parser_class=Parser)
    c = psub.add_parser(
        "create",
        help="New app (interactive in a terminal; non-interactive with --yes or without a TTY)",
        formatter_class=fmt,
        epilog=f"""{EXIT_CODES}

examples:
  hub project create estoque --stack python-fastapi --yes --json
  hub project create vendas --stack python-fastapi --template dashboard --yes
  hub project create estoque --name "Estoque" --requires database,storage --dir ./estoque --yes
  hub project create            (asks for whatever is missing, if there is a terminal)""",
    )
    c.add_argument("id", nargs="?", help="app id (2–32: a-z, 0-9, '-')")
    c.add_argument("--name", help="display name (default: capitalized id)")
    c.add_argument("--stack", help="stack (see `hub project stacks`; default python-fastapi)")
    c.add_argument(
        "--db", default=None, help="sqlite (default) or database URL (written to .env DATABASE_URL)"
    )
    c.add_argument("--requires", help="what the hub provisions: database,storage (default: database)")
    c.add_argument("--dir", type=Path, help="target folder (default: ./<id>)")
    c.add_argument("--template", choices=project.TEMPLATES, default=None,
                   help="basic (default) or dashboard (KPIs, charts, table, filter and export)")  # fmt: skip
    c.add_argument("--with", dest="with_", default=None, help="extras: dashboards (= --template dashboard)")
    c.add_argument("--yes", "-y", action="store_true", help="no questions: use the defaults")
    c.add_argument("--json", action="store_true", help="JSON output")
    s = psub.add_parser("stacks", help="List the available stacks")
    s.add_argument("--json", action="store_true")


def add_check(sub: Subparsers) -> None:
    fmt = FMT
    ck = sub.add_parser("check", help="ruff, ty, pytest+coverage, manifest, openapi and local conformance",
                        epilog=f"{EXIT_CODES}\n\nexample:\n  hub check --only ruff,pytest --json",
                        formatter_class=fmt)  # fmt: skip
    ck.add_argument("--dir", type=Path, default=Path())
    ck.add_argument("--only", help="comma-separated steps: " + ",".join(n for n, _ in check_mod.STEPS))
    ck.add_argument("--full", action="store_true", help="also the full contract suite (conformance.json)")
    ck.add_argument("--json", action="store_true")


def add_pack(sub: Subparsers) -> None:
    k = sub.add_parser(
        "pack",
        help="Build the .hubapp package from the app folder (CI, or the manual import on the Deployments screen)",
    )
    k.add_argument("--dir", type=Path, default=Path())
    k.add_argument("--image", help="image by digest (name@sha256:…); alternative: --build-dir")
    k.add_argument("--build-dir", type=Path, help="build context: the hub builds the package image")
    k.add_argument("--dockerfile", default="Dockerfile", help="Dockerfile inside --build-dir")
    k.add_argument("--out", type=Path)


def add_dev(sub: Subparsers) -> None:
    fmt = FMT
    d = sub.add_parser("dev", help="Lightweight local hub (no Docker) with the app inside the shell",
                       epilog="examples:\n  hub dev                       (template project: starts the app with reload)\n"
                       "  hub dev --upstream http://127.0.0.1:9000", formatter_class=fmt)  # fmt: skip
    d.add_argument("--dir", type=Path, default=Path())
    d.add_argument("--upstream", help="app already running (otherwise the CLI starts the project app)")
    d.add_argument("--port", type=int, default=4173)
    d.add_argument("--role", default="admin")
    d.add_argument("--limits", action="store_true")
    d.add_argument("command", nargs="*")


def add_login(sub: Subparsers) -> None:
    fmt = FMT
    lg = sub.add_parser("login", help="Save the hub and the token (the token is prompted without echo)",
                        epilog=f"{EXIT_CODES}\n\nexample:\n  hub login --hub https://hub.example.com",
                        formatter_class=fmt)  # fmt: skip
    lg.add_argument("--hub", required=True)
    lg.add_argument("--token", help="AVOID: use HUB_TOKEN, --token-file or the no-echo prompt")
    lg.add_argument("--token-file", type=Path, help="file with the token (e.g. provided by the CI vault)")
    lg.add_argument("--json", action="store_true")


def add_deploy(sub: Subparsers) -> None:
    fmt = FMT
    dp = sub.add_parser("deploy", help="Package and send to the hub (production stays pending approval)",
                        epilog=f"{EXIT_CODES}\n\nexamples:\n  hub deploy --env staging\n"
                        "  hub deploy --env production --hub https://hub.example.com --json", formatter_class=fmt)  # fmt: skip
    dp.add_argument("--env", required=True, choices=["staging", "production"])
    dp.add_argument("--dir", type=Path, default=Path())
    dp.add_argument("--hub", help="hub URL (default: the one from `hub login` or HUB_URL)")
    dp.add_argument("--token", help="AVOID: use `hub login` or HUB_TOKEN")
    dp.add_argument("--env-file", type=Path, help="values for requires (default: deploy.<env>.env)")
    dp.add_argument("--yes", "-y", action="store_true", help="no questions")
    dp.add_argument("--json", action="store_true")


def add_mcp(sub: Subparsers) -> None:
    m = sub.add_parser("mcp", help="stdio MCP server that forwards to the hub MCP with a token")
    m.add_argument("--hub", default=os.environ.get("HUB_URL", "http://localhost:8080"))
    m.add_argument("--token", help="AVOID: use HUB_TOKEN or --token-file")
    tf = os.environ.get("HUB_TOKEN_FILE")
    m.add_argument("--token-file", type=Path, default=Path(tf) if tf else None)
    m.add_argument("--path", default=os.environ.get("HUB_MCP_PATH", "/mcp"))


def interactive_tty(a: argparse.Namespace) -> bool:
    return interactive(a)


def interactive(a: argparse.Namespace) -> bool:
    return bool(not a.yes and sys.stdin.isatty() and sys.stdout.isatty())


def ask(label: str, default: str | None = None) -> str:
    hint = f" [{default}]" if default else ""
    v = input(f"{label}{hint}: ").strip()
    return v or (default or "")


def cmd_create(a: argparse.Namespace) -> int:
    tty = interactive(a)
    app_id = a.id or (ask("Id do app (ex.: estoque)") if tty else None)
    if not app_id:
        raise CliError("informe o id do app (ex.: hub project create estoque --yes)", VALIDATION)
    project.validate_id(app_id)
    default_name = app_id.replace("-", " ").capitalize()
    name = a.name or (ask("Display name", default_name) if tty else default_name)
    ids = [s["id"] for s in project.stacks()]
    stack = a.stack or (ask(f"Stack ({', '.join(ids)})", ids[0]) if tty else ids[0])
    db = a.db or (ask("Local database (sqlite or URL)", "sqlite") if tty else "sqlite")
    req = a.requires or (ask("The app needs (database,storage)", "database") if tty else None)
    directory = a.dir or Path(app_id)
    template = project.parse_template(a.template, a.with_)
    out = project.create(app_id, name, stack, db, project.parse_requires(req), directory, template)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(
            f"App {app_id} created in {out['dir']}\nNext steps:\n  cd {directory}\n  uv sync\n  hub check\n  hub dev"
        )
    return 0


def cmd_stacks(a: argparse.Namespace) -> int:
    rows = project.stacks()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['id']:<18} {r['name']} — {r['description']}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    only = [s.strip() for s in (a.only or "").split(",") if s.strip()] or None
    known = {n for n, _ in check_mod.STEPS} | {check_mod.FULL[0]}
    if only and set(only) - known:
        raise CliError(f"--only: unknown steps {', '.join(sorted(set(only) - known))}", VALIDATION)
    results = check_mod.run(a.dir, only, a.full or bool(only and check_mod.FULL[0] in only))
    ok = all(r["ok"] for r in results)
    if a.json:
        print(json.dumps({"ok": ok, "steps": results}, ensure_ascii=False))
    else:
        for r in results:
            print(f"{'ok ' if r['ok'] else 'FAILED'} {r['step']:<13} {r['seconds']:>6.1f}s")
            if not r["ok"]:
                print("    " + r["detail"].replace("\n", "\n    ")[-4000:])
        print("hub check: green" if ok else "hub check: failed")
    return 0 if ok else FAIL


def cmd_project(a: argparse.Namespace) -> int:
    return cmd_create(a) if a.pcmd == "create" else cmd_stacks(a)


def cmd_login(a: argparse.Namespace) -> int:
    tok = a.token or (a.token_file.read_text(encoding="utf-8").strip() if a.token_file else None)
    out = deploy_mod.login(a.hub, tok)
    print(json.dumps(out) if a.json else f"login salvo em {out['saved']} ({out['hub']})")
    return 0


def cmd_deploy(a: argparse.Namespace) -> int:
    out = deploy_mod.deploy(a.dir, a.env, a.hub, a.token, a.env_file, interactive_tty(a))
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(f"{out['app']} {out['version']} → {out['env']}: {out['message']}")
    if out.get("url"):
        print(f"  abrir: {out['url']}")
    return 0


def cmd_pack(a: argparse.Namespace) -> int:
    m = json.loads((a.dir / "manifest.json").read_text(encoding="utf-8"))
    out = a.out or Path(f"{m['id']}-{m['version']}.hubapp")
    print(f"{pack(a.dir, a.image, out, a.build_dir, a.dockerfile)}  {out}")
    return 0


def cmd_dev(a: argparse.Namespace) -> int:
    if not a.upstream:
        return dev_mod.run_project(a.dir, a.port, a.role)
    return dev_mod.run(a.dir, a.upstream, a.port, a.role, a.limits, [c for c in a.command if c != "--"])


def cmd_mcp(a: argparse.Namespace) -> int:
    token, warn = mcp_mod.resolve_token(a.token, a.token_file, os.environ.get("HUB_TOKEN"))
    if warn:
        print(f"hub mcp: {warn}", file=sys.stderr)
    return mcp_mod.run(a.hub, token, a.path)


COMMANDS = {
    "project": cmd_project,
    "login": cmd_login,
    "deploy": cmd_deploy,
    "check": cmd_check,
    "pack": cmd_pack,
    "dev": cmd_dev,
    "mcp": cmd_mcp,
}


def main(argv: list[str] | None = None) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:  # invalid usage (2) or --help (0)
        return e.code if isinstance(e.code, int) else VALIDATION
    try:
        return COMMANDS[a.cmd](a)
    except CliError as e:
        if getattr(a, "json", False):
            print(json.dumps({"ok": False, "error": str(e), "code": e.code}, ensure_ascii=False))
        else:
            print(f"erro: {e}", file=sys.stderr)
        return e.code
    except (PackError, ValueError, OSError, KeyError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return FAIL


def run() -> None:
    for s in (sys.stdout, sys.stderr):  # redirected output on Windows (cp1252) must not break on → · …
        reconfigure = getattr(s, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
