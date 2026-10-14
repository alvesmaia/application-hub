"""Entry point for `hub` (project, dev, check, pack, mcp)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn, override

from . import check as check_mod
from . import deploy as deploy_mod
from . import dev as dev_mod
from . import mcp as mcp_mod
from . import project
from .errors import FAIL, VALIDATION, CliError
from .pack import PackError, pack

EXIT_CODES = """códigos de saída: 0 ok · 1 falha (checagem/etapa) · 2 validação (parâmetro ausente ou inválido) ·
3 autenticação · 4 hub inacessível"""

EPILOG = f"""{EXIT_CODES}

exemplos:
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
        print(f"{self.prog}: erro: {message}", file=sys.stderr)
        sys.exit(VALIDATION)


def parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    p = Parser(prog="hub", description="CLI da Application Hub", epilog=EPILOG, formatter_class=fmt)
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    pr = sub.add_parser(
        "project", help="Cria apps a partir dos templates", epilog=EPILOG, formatter_class=fmt
    )
    psub = pr.add_subparsers(dest="pcmd", required=True, parser_class=Parser)
    c = psub.add_parser(
        "create",
        help="Novo app (interativo no terminal; não interativo com --yes ou sem TTY)",
        formatter_class=fmt,
        epilog=f"""{EXIT_CODES}

exemplos:
  hub project create estoque --stack python-fastapi --yes --json
  hub project create vendas --stack python-fastapi --template dashboard --yes
  hub project create estoque --name "Estoque" --requires database,storage --dir ./estoque --yes
  hub project create            (pergunta o que faltar, se houver terminal)""",
    )
    c.add_argument("id", nargs="?", help="id do app (2–32: a-z, 0-9, '-')")
    c.add_argument("--name", help="nome exibido (padrão: id capitalizado)")
    c.add_argument("--stack", help="stack (veja `hub project stacks`; padrão python-fastapi)")
    c.add_argument("--db", default=None, help="sqlite (padrão) ou URL do banco (vai para .env DATABASE_URL)")
    c.add_argument("--requires", help="o que o hub provisiona: database,storage (padrão: database)")
    c.add_argument("--dir", type=Path, help="pasta destino (padrão: ./<id>)")
    c.add_argument("--template", choices=project.TEMPLATES, default=None,
                   help="basic (padrão) ou dashboard (KPIs, gráficos, tabela, filtro e exportação)")  # fmt: skip
    c.add_argument("--with", dest="with_", default=None, help="extras: dashboards (= --template dashboard)")
    c.add_argument("--yes", "-y", action="store_true", help="sem perguntas: usa os padrões")
    c.add_argument("--json", action="store_true", help="saída em JSON")
    s = psub.add_parser("stacks", help="Lista as stacks disponíveis")
    s.add_argument("--json", action="store_true")

    ck = sub.add_parser("check", help="ruff, ty, pytest+cobertura, manifesto, openapi e conformidade local",
                        epilog=f"{EXIT_CODES}\n\nexemplo:\n  hub check --only ruff,pytest --json",
                        formatter_class=fmt)  # fmt: skip
    ck.add_argument("--dir", type=Path, default=Path())
    ck.add_argument("--only", help="etapas separadas por vírgula: " + ",".join(n for n, _ in check_mod.STEPS))
    ck.add_argument(
        "--full", action="store_true", help="também a suíte completa do contrato (conformance.json)"
    )
    ck.add_argument("--json", action="store_true")

    k = sub.add_parser("pack", help="Gera o pacote .hubapp a partir da pasta do app")
    k.add_argument("--dir", type=Path, default=Path())
    k.add_argument("--image", help="imagem por digest (nome@sha256:…); alternativa: --build-dir")
    k.add_argument("--build-dir", type=Path, help="contexto de build: o hub constrói a imagem do pacote")
    k.add_argument("--dockerfile", default="Dockerfile", help="Dockerfile dentro de --build-dir")
    k.add_argument("--out", type=Path)

    d = sub.add_parser("dev", help="Hub local leve (sem Docker) com o app dentro do shell",
                       epilog="exemplos:\n  hub dev                       (projeto do template: sobe o app com recarga)\n"
                       "  hub dev --upstream http://127.0.0.1:9000", formatter_class=fmt)  # fmt: skip
    d.add_argument("--dir", type=Path, default=Path())
    d.add_argument("--upstream", help="app já rodando (senão a CLI sobe o app do projeto)")
    d.add_argument("--port", type=int, default=4173)
    d.add_argument("--role", default="admin")
    d.add_argument("--limits", action="store_true")
    d.add_argument("command", nargs="*")

    lg = sub.add_parser("login", help="Salva o hub e o token (o token é pedido sem eco)",
                        epilog=f"{EXIT_CODES}\n\nexemplo:\n  hub login --hub https://hub.exemplo.com",
                        formatter_class=fmt)  # fmt: skip
    lg.add_argument("--hub", required=True)
    lg.add_argument("--token", help="EVITE: use HUB_TOKEN, --token-file ou a pergunta sem eco")
    lg.add_argument("--token-file", type=Path, help="arquivo com o token (ex.: entregue pelo cofre do CI)")
    lg.add_argument("--json", action="store_true")
    dp = sub.add_parser("deploy", help="Empacota e envia ao hub (produção fica pendente de aprovação)",
                        epilog=f"{EXIT_CODES}\n\nexemplos:\n  hub deploy --env staging\n"
                        "  hub deploy --env production --hub https://hub.exemplo.com --json", formatter_class=fmt)  # fmt: skip
    dp.add_argument("--env", required=True, choices=["staging", "production"])
    dp.add_argument("--dir", type=Path, default=Path())
    dp.add_argument("--hub", help="URL do hub (padrão: o do `hub login` ou HUB_URL)")
    dp.add_argument("--token", help="EVITE: use `hub login` ou HUB_TOKEN")
    dp.add_argument("--env-file", type=Path, help="valores de requires (padrão: deploy.<env>.env)")
    dp.add_argument("--yes", "-y", action="store_true", help="sem perguntas")
    dp.add_argument("--json", action="store_true")
    m = sub.add_parser("mcp", help="Servidor MCP por stdio que repassa ao MCP do hub com um token")
    m.add_argument("--hub", default=os.environ.get("HUB_URL", "http://localhost:8080"))
    m.add_argument("--token", help="EVITE: use HUB_TOKEN ou --token-file")
    tf = os.environ.get("HUB_TOKEN_FILE")
    m.add_argument("--token-file", type=Path, default=Path(tf) if tf else None)
    m.add_argument("--path", default=os.environ.get("HUB_MCP_PATH", "/mcp"))
    return p


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
    name = a.name or (ask("Nome exibido", default_name) if tty else default_name)
    ids = [s["id"] for s in project.stacks()]
    stack = a.stack or (ask(f"Stack ({', '.join(ids)})", ids[0]) if tty else ids[0])
    db = a.db or (ask("Banco local (sqlite ou URL)", "sqlite") if tty else "sqlite")
    req = a.requires or (ask("O app precisa de (database,storage)", "database") if tty else None)
    directory = a.dir or Path(app_id)
    template = project.parse_template(a.template, a.with_)
    out = project.create(app_id, name, stack, db, project.parse_requires(req), directory, template)
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(
            f"App {app_id} criado em {out['dir']}\nPróximos passos:\n  cd {directory}\n  uv sync\n  hub check\n  hub dev"
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
        raise CliError(f"--only: etapas desconhecidas {', '.join(sorted(set(only) - known))}", VALIDATION)
    results = check_mod.run(a.dir, only, a.full or bool(only and check_mod.FULL[0] in only))
    ok = all(r["ok"] for r in results)
    if a.json:
        print(json.dumps({"ok": ok, "steps": results}, ensure_ascii=False))
    else:
        for r in results:
            print(f"{'ok ' if r['ok'] else 'FALHOU'} {r['step']:<13} {r['seconds']:>6.1f}s")
            if not r["ok"]:
                print("    " + r["detail"].replace("\n", "\n    ")[-4000:])
        print("hub check: verde" if ok else "hub check: com falhas")
    return 0 if ok else FAIL


def main(argv: list[str] | None = None) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as e:  # invalid usage (2) or --help (0)
        return e.code if isinstance(e.code, int) else VALIDATION
    try:
        if a.cmd == "project":
            return cmd_create(a) if a.pcmd == "create" else cmd_stacks(a)
        if a.cmd == "login":
            tok = a.token or (a.token_file.read_text(encoding="utf-8").strip() if a.token_file else None)
            out = deploy_mod.login(a.hub, tok)
            print(json.dumps(out) if a.json else f"login salvo em {out['saved']} ({out['hub']})")
            return 0
        if a.cmd == "deploy":
            out = deploy_mod.deploy(a.dir, a.env, a.hub, a.token, a.env_file, interactive_tty(a))
            if a.json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                print(f"{out['app']} {out['version']} → {out['env']}: {out['message']}")
                if out.get("url"):
                    print(f"  abrir: {out['url']}")
            return 0
        if a.cmd == "check":
            return cmd_check(a)
        if a.cmd == "pack":
            m = json.loads((a.dir / "manifest.json").read_text(encoding="utf-8"))
            out = a.out or Path(f"{m['id']}-{m['version']}.hubapp")
            print(f"{pack(a.dir, a.image, out, a.build_dir, a.dockerfile)}  {out}")
            return 0
        if a.cmd == "dev":
            if not a.upstream:
                return dev_mod.run_project(a.dir, a.port, a.role)
            return dev_mod.run(
                a.dir, a.upstream, a.port, a.role, a.limits, [c for c in a.command if c != "--"]
            )
        token, warn = mcp_mod.resolve_token(a.token, a.token_file, os.environ.get("HUB_TOKEN"))
        if warn:
            print(f"hub mcp: {warn}", file=sys.stderr)
        return mcp_mod.run(a.hub, token, a.path)
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
