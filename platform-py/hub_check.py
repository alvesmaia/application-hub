"""Qualidade multiplataforma: `uv run hub-check [--fast|--all]`. Para na primeira falha."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
COV = [
    "hub.auth",
    "hub.vault",
    "hub.state",
    "hub.admin",
    "hub.gateway",
    "hub.ux",
    "hub.connections",
    "hub.storage",
    "graphsim",
    "hub.insight",
    "hub.ops",
    "hub.api",
]


# o `uv run` externo exporta VIRTUAL_ENV (venv do hub); o `ty` dos subprojetos o herdaria e nao acharia o SDK
ENV = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def _run(path: Path) -> list[str]:
    """Prefixo `uv run` do projeto; apps nao resolvem o SDK sozinhos: usa o do repositorio."""
    text = (path / "pyproject.toml").read_text(encoding="utf-8")
    if "hub-sdk" in text:
        sdk = REPO / "platform" / "sdk-python"
        return ["uv", "run", "--no-project", "--with-editable", str(sdk), "--with-editable", f"{path}[dev]"]
    return ["uv", "run", "--project", str(path)]


def _projects() -> list[Path]:
    apps = sorted((REPO / "platform" / "demo-apps-py").glob("*"))
    projs = [ROOT / "cli", REPO / "platform" / "sdk-python", *apps]
    return [p for p in projs if (p / "pyproject.toml").exists()]


def ty_files(files: list[str]) -> int:
    """Gancho do pre-commit: `ty` nos arquivos dados, cada um no ambiente do seu projeto."""
    groups: dict[Path, list[str]] = {}
    for f in files:
        p = Path(f).resolve()
        owner = next((x for x in _projects() if x in p.parents), ROOT)
        groups.setdefault(owner, []).append(str(p))
    rc = 0
    for owner, fs in groups.items():
        cmd = [*(["uv", "run"] if owner == ROOT else _run(owner)), "ty", "check", *fs]
        rc |= subprocess.run(cmd, cwd=owner, check=False, env=ENV).returncode  # noqa: S603
    return rc


def hub_steps(fast: bool) -> list[tuple[str, list[str], Path]]:
    run = ["uv", "run"]
    steps = [
        ("ruff", [*run, "ruff", "check"]),
        ("ruff-format", [*run, "ruff", "format", "--check"]),
        ("ty", [*run, "ty", "check", "hub", "graphsim", "tests"]),
    ]
    if not fast:
        cov = [f"--cov={m}" for m in COV]
        steps += [
            ("audit", ["uv", "audit", "--preview-features", "audit-command"]),
            (
                "pytest",
                [
                    *run,
                    "pytest",
                    "-q",
                    *cov,
                    "--cov-branch",
                    "--cov-report=term-missing",
                    "--cov-fail-under=80",
                ],
            ),
        ]
    return [(f"hub/{n}", c, ROOT) for n, c in steps]


def project_steps(path: Path, fast: bool) -> list[tuple[str, list[str], Path]]:
    run = _run(path)
    srcs = [d for d in ("src", "hub_cli", "tests") if (path / d).is_dir()] or ["."]
    steps = [
        ("ruff", [*run, "ruff", "check", str(path)]),
        ("ruff-format", [*run, "ruff", "format", "--check", str(path)]),
        ("ty", [*run, "ty", "check", *srcs]),
    ]
    if not fast and (path / "tests").is_dir():
        steps.append(("pytest", [*run, "pytest", "-q"]))
    return [(f"{path.name}/{n}", c, path) for n, c in steps]


def main() -> int:
    ap = argparse.ArgumentParser(prog="hub-check")
    ap.add_argument("--fast", action="store_true", help="so ruff, formato e ty")
    ap.add_argument("--all", action="store_true", help="tambem cli, sdk e cada app")
    ap.add_argument("--ty-files", nargs="+", metavar="ARQ", help="so ty nesses arquivos (pre-commit)")
    a = ap.parse_args()
    if a.ty_files:
        return ty_files(a.ty_files)
    steps = hub_steps(a.fast)
    if a.all:
        projs = _projects()
        for p in projs:
            steps += project_steps(p, a.fast)
    results: list[tuple[str, bool, float]] = []
    for label, cmd, cwd in steps:
        print(f"\n=== {label}: {' '.join(cmd)}", flush=True)
        t = time.monotonic()
        ok = subprocess.run(cmd, cwd=cwd, check=False, env=ENV).returncode == 0  # noqa: S603
        results.append((label, ok, time.monotonic() - t))
        if not ok:
            break
    print("\n--- resumo")
    for label, ok, dt in results:
        print(f"{'OK  ' if ok else 'FAIL'} {label} ({dt:.1f}s)")
    bad = [r for r in results if not r[1]]
    print(f"\nFALHOU em {bad[0][0]}" if bad else f"\nVERDE ({len(results)}/{len(steps)} etapas)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
