"""`hub dev`: runs one app alone inside the real shell, without Docker or Postgres (FAQ 7).

The minimal in-process hub lives in `hub.devapp` (platform package, bundled in the CLI along with the assets of the
shell). Without `--upstream`, the CLI also starts the app itself (`uv run uvicorn … --reload`) with SQLite in
`.hub/dev.db`, storage in `.hub/storage/` (same `hub_sdk.storage` API) and in-process tasks.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import socket
import subprocess
import sys
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import IO

from .errors import CliError


def banner(
    dev: object, entry: object, keys: list[str], ignored: list[str], port: int, role: str, upstream: str
) -> str:
    app_id, name, version = (
        getattr(entry, "id", ""),
        getattr(entry, "name", ""),
        getattr(entry, "version", ""),
    )
    base = f"http://localhost:{port}"
    s = (
        f"\nhub dev · {name} ({app_id} v{version})\n  Shell:       {base}/apps/{app_id}\n"
        f"  Seções:      {base}/apps/{app_id}/<seção>\n  Fragmentos:  {base}/_frag/{app_id}/… → {upstream}\n"
        f"  Identidade:  {getattr(dev, 'DEV_USER', '')} (papel: {role})\n"
        f"  Variáveis:   {', '.join(keys) + ' (de .hub/dev.env)' if keys else 'nenhuma'}\n"
    )
    if ignored:
        s += f"  Ignoradas:   {', '.join(ignored)} (não declaradas em manifest.env)\n"
    return s


def bundled_assets() -> None:
    """Outside the repository, points the in-process hub at the assets bundled in the CLI."""
    root = Path(str(resources.files("hub_cli"))) / "_assets"
    if (root / "shell").is_dir() and "HUB_ASSET_DIRS" not in os.environ:
        os.environ["HUB_ASSET_DIRS"] = os.pathsep.join([str(root / "shell"), str(root / "web")])


def devapp_module() -> ModuleType:
    bundled_assets()
    try:
        from hub import devapp
    except ImportError as e:
        raise CliError(f"hub dev: pacote da plataforma (`hub`) ausente nesta instalação ({e})", 2) from e
    return devapp


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            k, sep, v = raw.strip().partition("=")
            if sep and k and not k.startswith("#"):
                out[k.strip()] = v.strip()
    return out


def app_env(directory: Path, app_id: str, port: int) -> dict[str, str]:
    """App environment in dev: what the platform would inject, with SQLite and a local folder."""
    hub = directory.resolve() / ".hub"
    (hub / "scratch").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env.update(
        {
            "HUB_APP_ID": app_id,
            "PORT": str(port),
            "DATABASE_URL": f"sqlite:///{(hub / 'dev.db').as_posix()}",
            "HUB_STORAGE_LOCAL_DIR": str(hub / "storage"),
            "HUB_SCRATCH_DIR": str(hub / "scratch"),
            "HUB_INTERNAL_TOKEN": secrets.token_urlsafe(24),
            "HUB_JOBS_MODE": "inline",
            "PYTHONUTF8": "1",  # correct accents in the Windows console (cp1252)
            "HUB_SQL_LOCAL_FILE": str(local_connections(directory)),
        }
    )
    env.update(read_env(directory / ".env"))
    return env


def local_connections(directory: Path) -> Path:
    """`.hub/connections.toml` (local SQL connections for `hub_sdk.sql`): on first run copies `dev/connections.toml`
    from the project and creates the sample SQLite databases `.hub/<name>.db` from `dev/<name>.sql`."""
    import sqlite3

    hub = directory.resolve() / ".hub"
    hub.mkdir(parents=True, exist_ok=True)
    target, seed = hub / "connections.toml", directory / "dev" / "connections.toml"
    if not target.is_file() and seed.is_file():
        target.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    for script in sorted((directory / "dev").glob("*.sql")):
        db = hub / f"{script.stem}.db"
        if not db.is_file():
            con = sqlite3.connect(db)
            try:
                con.executescript(script.read_text(encoding="utf-8"))
                con.commit()
            finally:
                con.close()
    return target


def app_command(directory: Path, pkg: str, port: int, reload: bool = True) -> list[str]:
    cmd = ["uv", "run", "--project", str(directory), "uvicorn", f"{pkg}.app:create_app", "--factory",
           "--host", "127.0.0.1", "--port", str(port)]  # fmt: skip
    if reload:
        cmd += ["--reload", "--reload-dir", str(directory / "src")]
    return cmd


def run(
    directory: Path,
    upstream: str,
    port: int,
    role: str,
    limits: bool,
    command: list[str],
    env: dict[str, str] | None = None,
) -> int:
    if not (directory / "manifest.json").is_file():
        raise CliError(f"manifest.json não encontrado em {directory.resolve()}", 2)
    devapp = devapp_module()
    import uvicorn

    os.environ.setdefault("HUB_DATA_DIR", str(directory.resolve() / ".hub" / "hubdata"))
    asgi, entry = devapp.dev_asgi(directory, upstream, role)
    if env and env.get("HUB_INTERNAL_TOKEN"):  # local hub: the app's token (no 401 on /_hub/*)
        os.environ["HUB_INTERNAL_TOKEN_" + entry.id.upper().replace("-", "_")] = env["HUB_INTERNAL_TOKEN"]
    _, _, keys, ignored = devapp.load_app(directory, upstream)
    if limits:
        print("hub dev: --limits não é suportado na CLI Python; seguindo sem limites.", file=sys.stderr)
    print(banner(devapp, entry, keys, ignored, port, role, upstream), flush=True)
    child = spawn(command, directory, env) if command else None
    try:
        uvicorn.run(asgi, host="127.0.0.1", port=port, log_level="warning")
    finally:
        if child:
            stop(child)
    return 0


def spawn(
    cmd: list[str], cwd: Path, env: dict[str, str] | None, out: IO[str] | None = None
) -> subprocess.Popen[str]:
    """Child process in its own group (`uv run` has grandchildren: shutdown takes the whole tree)."""
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=out, text=True, creationflags=flags)  # noqa: S603
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=out, text=True, start_new_session=True)  # noqa: S603


def stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)  # noqa: S603, S607
    else:
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_project(directory: Path, port: int, role: str) -> int:
    """`hub dev` without `--upstream`: starts the project's app (uvicorn --reload) and the local hub in front of it."""
    import json

    mf = directory / "manifest.json"
    if not mf.is_file():
        raise CliError(f"manifest.json não encontrado em {directory.resolve()}", 2)
    app_id = str(json.loads(mf.read_text(encoding="utf-8")).get("id", ""))
    if not (directory / "src" / app_id.replace("-", "_")).is_dir():
        raise CliError("hub dev sem --upstream precisa do layout do template (src/<pacote>/app.py)", 2)
    app_port = free_port()
    env = app_env(directory, app_id, app_port)
    cmd = app_command(directory, app_id.replace("-", "_"), app_port)
    print(f"hub dev: app em http://127.0.0.1:{app_port} (banco {env['DATABASE_URL']})")
    return run(directory, f"http://127.0.0.1:{app_port}", port, role, False, cmd, env)
