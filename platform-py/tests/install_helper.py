"""Runs scripts/install.sh in a temp copy of platform-py, with scripted answers and fake commands on the PATH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
COPIED = (
    "scripts/install.sh",
    "scripts/install/net.sh",
    "scripts/install/doctor.sh",
    "prod.env.example",
    "docker-compose.yml",
)
# no engine on the PATH: a fake docker answers nothing, so a run without --config-only stops at the compose check
NO_ENGINE = {"docker": "exit 1"}

# Fake engine that gets through the compose check and records every build (arguments + network variables) in
# $FAKE_LOG; `compose ... build` fails afterwards so the run stops right after the build steps.
RECORDING_DOCKER = r"""
case "$1 ${2:-}" in
  "compose version") exit 0 ;;
  "--version "*) echo "Docker version 27.0"; exit 0 ;;
  "image inspect") exit 1 ;;
esac
case " $* " in
  *" build "*)
    echo "ARGS $*" >> "$FAKE_LOG"
    env | grep -E '^(HTTPS?_PROXY|https?_proxy|NO_PROXY|no_proxy|EXTRA_CA_PEM|PIP_INDEX_URL|UV_DEFAULT_INDEX|IMAGE_PREFIX|APK_MIRROR|GOPROXY)=' >> "$FAKE_LOG"
    [ "$1" = compose ] && exit 1 ;;
esac
exit 0
"""


def run_install(
    tmp_path: Path,
    args: tuple[str, ...] = (),
    answers: list[str] | None = None,
    fakes: dict[str, str] | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`fakes` maps a command name to the body of a shell script placed first on the PATH."""
    pp = tmp_path / "platform-py"
    (pp / "scripts" / "install").mkdir(parents=True, exist_ok=True)
    for f in COPIED:
        shutil.copy(ROOT / f, pp / f)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    for name, body in (NO_ENGINE | (fakes or {})).items():
        (fakebin / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
        (fakebin / name).chmod(0o755)
    path = f"{fakebin}:{Path(str(BASH)).parent}:/usr/bin:/bin"
    env = {"PATH": path, "HOME": str(tmp_path), **(env_extra or {})}
    if answers is not None:
        tty = tmp_path / "answers.txt"
        tty.write_text("\n".join(answers) + "\n", encoding="utf-8", newline="\n")
        env["INSTALL_TTY"] = str(tty)
    return subprocess.run(  # noqa: S603
        [str(BASH), "scripts/install.sh", *args],
        cwd=pp,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def prod_env(tmp_path: Path) -> dict[str, str]:
    text = (tmp_path / "platform-py" / ".prod.env").read_text(encoding="utf-8")
    return dict(l.split("=", 1) for l in text.splitlines() if l and not l.startswith("#") and "=" in l)  # noqa: E741
