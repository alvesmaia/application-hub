#!/usr/bin/env python3
"""Updates the hub without downtime and rolls back automatically if the new version fails (docs/ATUALIZACAO.md).

    python platform-py/scripts/hub_update.py --build            # rebuilds from the current code and swaps
    python platform-py/scripts/hub_update.py --image <image>   # uses a ready image (e.g. from GHCR)
    python platform-py/scripts/hub_update.py --rollback         # goes back to the previous version

How it works (the `<proj>-edge` edge only sends traffic to whoever answers /healthz):
  1. backup (databases + data volume) and the current image becomes `<proj>-web:rollback`;
  2. starts `<proj>-web-next` with the new image and the same hub configuration (env, volumes, networks, aliases);
  3. once the copy is ready (/readyz), recreates `<proj>-web` with the new image — the edge serves through the copy;
  4. watches the hub for `--watch` seconds; on failure, swaps back to the previous image;
  5. removes the copy.
Only the standard library and the machine's `docker`. Does not print environment variables (they contain secrets).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

READY = "import urllib.request as u,sys; sys.stdout.write(u.urlopen('http://127.0.0.1:8090/readyz', timeout=3).read().decode())"


def say(msg: str) -> None:
    print(f"hub-update: {msg}", flush=True)


def run(*args: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)  # noqa: S603  # fmt: skip
    if check and p.returncode != 0:
        raise SystemExit(
            f"hub-update: falhou: {' '.join(args[:4])}…\n{(p.stderr or p.stdout).strip()[-800:]}"
        )
    if not quiet and p.returncode != 0:
        say((p.stderr or p.stdout).strip()[-300:])
    return p


def inspect(name: str) -> dict[str, Any] | None:
    p = run("docker", "inspect", name, check=False, quiet=True)
    return json.loads(p.stdout)[0] if p.returncode == 0 else None


def compose_cmd(main: dict[str, Any], root: Path | None = None) -> list[str]:
    """The same `docker compose` that created the hub (files, folder and .env come from the container labels).
    With ``root``, the same files are taken from that checkout instead (the build uses the code next to this script)."""
    lb = main["Config"]["Labels"]
    wd = lb["com.docker.compose.project.working_dir"]

    def at(f: str) -> str:
        return str(root / f[len(wd) :].lstrip(r"\/")) if root and f.startswith(wd) else f

    cmd = ["docker", "compose", "-p", lb["com.docker.compose.project"]]
    cmd += ["--project-directory", str(root) if root else wd]
    for f in lb["com.docker.compose.project.config_files"].split(","):
        cmd += ["-f", at(f)]
    if env := lb.get("com.docker.compose.project.environment_file"):
        for f in env.split(","):
            cmd += ["--env-file", at(f)]
    return cmd


def ready(name: str) -> dict[str, Any] | None:
    p = run("docker", "exec", name, "python", "-c", READY, check=False, quiet=True)
    try:
        d = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("status") == "ready" else None


def wait_ready(name: str, timeout: float) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (d := ready(name)) is not None:
            return d
        st = inspect(name)
        if st is None or st["State"]["Status"] in ("exited", "dead"):
            raise RuntimeError(f"{name} parou ao subir (veja: docker logs {name})")
        time.sleep(2)
    raise RuntimeError(f"{name} não ficou pronto em {int(timeout)} s (veja: docker logs {name})")


def backup(proj: str, image: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for db, out in ((f"{proj}-db", "hub-db.sql"), (f"{proj}-app-db", "app-db.sql")):
        if inspect(db):
            with (dest / out).open("w", encoding="utf-8") as fh:
                p = subprocess.run(["docker", "exec", db, "pg_dumpall", "-U", "hub"], stdout=fh, check=False)  # noqa: S603, S607
            if p.returncode != 0:
                raise SystemExit(f"hub-update: backup de {db} falhou; nada foi trocado")
    # The hub keeps writing to /data: tar exits with 1 if a file changed while being read (warning); 2 = error.
    p = run("docker", "run", "--rm", "--entrypoint", "tar", "-v", f"{proj}-data:/d:ro", "-v", f"{dest.resolve()}:/b",
        image, "czf", "/b/hub-data.tgz", "--warning=no-file-changed", "-C", "/d", ".", check=False, quiet=True)  # fmt: skip
    if p.returncode > 1:
        raise SystemExit(
            f"hub-update: backup de {proj}-data falhou; nada foi trocado: {p.stderr.strip()[-300:]}"
        )
    say(f"backup em {dest}")


def start_next(main: dict[str, Any], image: str, name: str) -> None:
    """Copy of the hub with the same configuration as the running container (includes all compose overrides)."""
    run("docker", "rm", "-f", name, check=False, quiet=True)
    cfg, host = main["Config"], main["HostConfig"]
    args = ["docker", "run", "-d", "--name", name, "--label", "hub.update=next", "--restart", "no"]
    base = set((inspect(main["Image"]) or {}).get("Config", {}).get("Env") or [])
    for e in cfg.get("Env") or []:
        if (
            e not in base
        ):  # only what compose defined: the image environment (PATH, versions) comes from the new image
            args += ["-e", e]
    for b in host.get("Binds") or []:
        args += ["-v", b]
    for m in host.get("Mounts") or []:
        spec = f"type={m['Type']},target={m['Target']}" + (
            f",source={m['Source']}" if m.get("Source") else ""
        )
        args += ["--mount", spec + (",readonly" if m.get("ReadOnly") else "")]
    for g in host.get("GroupAdd") or []:
        args += ["--group-add", g]
    for h in host.get("ExtraHosts") or []:
        args += ["--add-host", h]
    if host.get("Memory"):
        args += ["--memory", str(host["Memory"])]
    if cfg.get("User"):
        args += ["--user", cfg["User"]]
    nets = list(main["NetworkSettings"]["Networks"])
    edge = [n for n in nets if n.endswith("-edge")]
    others = [n for n in nets if n not in edge]
    alias = lambda n: "app-hub-web-next" if n in edge else "app-hub-web"  # noqa: E731 — the edge and the apps
    first = (edge or others)[0]
    args += ["--network", first, "--network-alias", alias(first), image, *(cfg.get("Cmd") or [])]
    run(*args)
    for n in nets:
        if n != first:
            run("docker", "network", "connect", "--alias", alias(n), n, name)


def swap(main_name: str, next_name: str, image: str, timeout: float) -> str:
    """Swaps the hub for `image` without downtime; returns the version left running."""
    main = inspect(main_name)
    if main is None:
        raise RuntimeError(f"{main_name} sumiu durante a troca")
    say(f"subindo a cópia {next_name}")
    start_next(main, image, next_name)
    v = wait_ready(next_name, timeout).get("version", "?")
    say(f"cópia pronta (versão {v}); recriando {main_name}")
    time.sleep(3)  # the edge probes every 1 s: the copy is already in the list when the hub leaves
    service = main["Config"]["Labels"]["com.docker.compose.service"]
    run(*compose_cmd(main), "up", "-d", "--no-deps", service)
    v = wait_ready(main_name, timeout).get("version", "?")
    say(f"{main_name} pronto (versão {v})")
    return str(v)


def watch(main_name: str, edge: str, secs: int) -> bool:
    say(f"observando por {secs} s")
    fails = 0
    end = time.monotonic() + secs
    while time.monotonic() < end:
        ok = (
            ready(main_name) is not None
            and run(
                "docker",
                "exec",
                edge,
                "wget",
                "-qO-",
                "http://127.0.0.1:8090/healthz",
                check=False,
                quiet=True,
            ).returncode
            == 0
        )
        fails = 0 if ok else fails + 1
        if fails >= 3:
            return False
        time.sleep(5)
    return True


def drop(name: str) -> None:
    run("docker", "stop", "-t", "30", name, check=False, quiet=True)
    run("docker", "rm", "-f", name, check=False, quiet=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # accents in the Windows console
        getattr(stream, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Atualiza o hub sem parada (com volta automática).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--build", action="store_true", help="reconstrói a imagem do hub a partir do código atual"
    )
    src.add_argument("--image", help="imagem pronta da versão nova")
    src.add_argument(
        "--rollback", action="store_true", help="volta para a imagem anterior (<proj>-web:rollback)"
    )
    ap.add_argument("--project", default="app-hub", help="HUB_PROJECT da instalação (padrão: app-hub)")
    ap.add_argument(
        "--watch", type=int, default=300, help="segundos de observação depois da troca (padrão 300)"
    )
    ap.add_argument("--timeout", type=int, default=180, help="espera máxima para cada cópia ficar pronta")
    ap.add_argument("--backup-dir", default=str(Path.home() / "backup-hub"), help="pasta dos backups")
    ap.add_argument("--no-backup", action="store_true", help="pula o backup (não recomendado)")
    a = ap.parse_args()

    main_name, next_name, edge = f"{a.project}-web", f"{a.project}-web-next", f"{a.project}-edge"
    repo = f"{a.project}-web"
    main_c, edge_c = inspect(main_name), inspect(edge)
    if main_c is None:
        raise SystemExit(f"hub-update: {main_name} não existe")
    if edge_c is None or edge_c["State"]["Status"] != "running":
        raise SystemExit(
            f"hub-update: a borda {edge} não está no ar. Na primeira vez, suba a pilha com o compose atual "
            "(`up -d`): a borda assume a porta (~10 s de parada, uma vez só)."
        )
    current = main_c["Image"]
    # Tag now: under containerd, an image that loses `latest` disappears by ID (backup, rollback and return would fail).
    prev = f"{repo}:pre-update"
    if run("docker", "tag", current, prev, check=False, quiet=True).returncode != 0 and inspect(prev) is None:
        raise SystemExit(
            f"hub-update: a imagem em execução não tem referência; crie {prev} com a versão atual"
        )
    if a.rollback:
        if inspect(f"{repo}:rollback") is None:
            raise SystemExit("hub-update: não há imagem anterior (<proj>-web:rollback)")
        new = run("docker", "image", "inspect", "-f", "{{.Id}}", f"{repo}:rollback").stdout.strip()
        if new == current:
            say("a versão anterior é a que já está no ar; nada a fazer")
            return 0
    else:
        if a.build:
            say("construindo a imagem nova")
            run(
                *compose_cmd(main_c, Path(__file__).resolve().parents[1]),
                "build",
                main_c["Config"]["Labels"]["com.docker.compose.service"],
            )
        else:
            if inspect(a.image) is None:  # only pulls what does not exist here
                run("docker", "pull", a.image)
            run("docker", "tag", a.image, f"{repo}:latest")
        new = run("docker", "image", "inspect", "-f", "{{.Id}}", f"{repo}:latest").stdout.strip()
        if new == current:
            say("a imagem nova é a que já está no ar; nada a fazer")
            return 0
        if not a.no_backup:
            backup(a.project, prev, Path(a.backup_dir) / dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        run("docker", "tag", prev, f"{repo}:rollback")
    run("docker", "tag", new, f"{repo}:latest")  # compose uses <proj>-web(:latest)

    try:
        version = swap(main_name, next_name, new, a.timeout)
        healthy = watch(main_name, edge, a.watch)
    except RuntimeError as e:
        say(str(e))
        healthy = False
        if (m := inspect(main_name)) is not None and m["Image"] == current:  # the hub was not even swapped
            drop(next_name)
            run("docker", "tag", prev, f"{repo}:latest")
            say("nada foi trocado; o hub segue na versão anterior")
            return 1
    if healthy:
        drop(next_name)
        say(f"concluído: versão {version} no ar, sem parada. Volta: hub_update.py --rollback")
        return 0
    say("a versão nova falhou: voltando para a anterior")
    run("docker", "tag", prev, f"{repo}:latest")
    try:
        swap(main_name, next_name, prev, a.timeout)
    finally:
        drop(next_name)
    say(
        "voltou para a versão anterior. Dados alterados pela nova? Restaure o backup (docs/ATUALIZACAO.md §5)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
