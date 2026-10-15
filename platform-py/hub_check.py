"""Cross-platform quality checks: `uv run hub-check [--fast|--all]`. Stops at the first failure."""

from __future__ import annotations

import argparse
import json
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


MAX_FILE_LINES = 500
SIZE_BASELINE = ROOT / "size-baseline.json"
# source code under the size rule (AGENTS.md "Code size"); tests, generated files and vendored code stay out
SIZE_GLOBS = [
    "platform-py/hub/**/*.py",
    "platform-py/graphsim/**/*.py",
    "platform-py/scripts/**/*.py",
    "platform-py/cli/hub_cli/**/*.py",
    "platform/sdk-python/src/**/*.py",
    "platform/demo-apps-py/*/src/**/*.py",
    "platform/assets/**/*.js",
]


def oversized(counts: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Ratchet: a new file stays within the limit, a baselined one only shrinks, and it leaves the baseline once it fits."""
    errs = []
    for f, n in sorted(counts.items()):
        if f in baseline:
            if n > baseline[f]:
                errs.append(f"{f}: {n} lines (baseline {baseline[f]}); split it, do not grow it")
        elif n > MAX_FILE_LINES:
            errs.append(f"{f}: {n} lines (limit {MAX_FILE_LINES})")
    errs += [
        f"{f}: fits now; remove it from size-baseline.json"
        for f in sorted(baseline)
        if counts.get(f, 0) <= MAX_FILE_LINES
    ]
    return errs


def line_counts() -> dict[str, int]:
    files = {f for g in SIZE_GLOBS for f in REPO.glob(g) if "vendor" not in f.parts}
    return {f.relative_to(REPO).as_posix(): len(f.read_bytes().splitlines()) for f in files}


def check_sizes() -> int:
    baseline = json.loads(SIZE_BASELINE.read_text(encoding="utf-8"))
    errs = oversized(line_counts(), baseline)
    print(
        "\n".join(errs) or f"sizes OK (limit {MAX_FILE_LINES} lines; {len(baseline)} files in the baseline)"
    )
    return 1 if errs else 0


# the outer `uv run` exports VIRTUAL_ENV (the hub venv); the subprojects' `ty` would inherit it and not find the SDK
ENV = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def _run(path: Path) -> list[str]:
    """`uv run` prefix for the project; apps do not resolve the SDK on their own: they use the repository one."""
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
    """Pre-commit hook: `ty` on the given files, each in its own project environment."""
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
        ("sizes", [sys.executable, str(Path(__file__).resolve()), "--sizes"]),
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


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="hub-check")
    ap.add_argument("--fast", action="store_true", help="so ruff, formato e ty")
    ap.add_argument("--all", action="store_true", help="tambem cli, sdk e cada app")
    ap.add_argument("--ty-files", nargs="+", metavar="ARQ", help="so ty nesses arquivos (pre-commit)")
    ap.add_argument("--sizes", action="store_true", help="only the file-size ratchet")
    return ap.parse_args()


def run_steps(steps: list[tuple[str, list[str], Path]]) -> list[tuple[str, bool, float]]:
    """Runs the steps in order and stops at the first failure."""
    results: list[tuple[str, bool, float]] = []
    for label, cmd, cwd in steps:
        print(f"\n=== {label}: {' '.join(cmd)}", flush=True)
        t = time.monotonic()
        ok = subprocess.run(cmd, cwd=cwd, check=False, env=ENV).returncode == 0  # noqa: S603
        results.append((label, ok, time.monotonic() - t))
        if not ok:
            break
    return results


def summary(results: list[tuple[str, bool, float]], total: int) -> int:
    print("\n--- resumo")
    for label, ok, dt in results:
        print(f"{'OK  ' if ok else 'FAIL'} {label} ({dt:.1f}s)")
    bad = [r for r in results if not r[1]]
    print(f"\nFALHOU em {bad[0][0]}" if bad else f"\nVERDE ({len(results)}/{total} etapas)")
    return 1 if bad else 0


def main() -> int:
    a = _args()
    if a.sizes:
        return check_sizes()
    if a.ty_files:
        return ty_files(a.ty_files)
    steps = hub_steps(a.fast)
    for p in _projects() if a.all else []:
        steps += project_steps(p, a.fast)
    return summary(run_steps(steps), len(steps))


if __name__ == "__main__":
    sys.exit(main())
