"""Kit do desenvolvedor: `hub project`, `hub check` e `hub dev` (sem Docker)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from hub_cli import check, dev, project
from hub_cli.errors import CliError
from hub_cli.main import main


@pytest.fixture
def wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    w = tmp_path / "hub_sdk-1.0.0-py3-none-any.whl"
    w.write_bytes(b"PK")
    monkeypatch.setattr(project, "sdk_wheel", lambda: w)
    return w


def test_stacks(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["project", "stacks", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "python-fastapi"
    assert main(["project", "stacks"]) == 0 and "python-fastapi" in capsys.readouterr().out


def test_create_non_interactive(tmp_path: Path, wheel: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = tmp_path / "estoque"
    args = ["project", "create", "estoque", "--stack", "python-fastapi", "--dir", str(d), "--yes", "--json"]
    assert main([*args, "--requires", "database,storage"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "estoque" and "src/estoque/app.py" in out["files"]
    m = json.loads((d / "manifest.json").read_text())
    assert m["id"] == "estoque" and m["requires"]["storage"] is True
    assert check.manifest_problems(m) == []
    assert (d / ".gitignore").is_file() and (d / "vendor" / wheel.name).is_file()
    assert "__APP_ID__" not in (d / "src/estoque/app.py").read_text()
    # perfil da suíte completa (hub check --full) e o demo.css do shell, também fora do wheel
    assert json.loads((d / "conformance.json").read_text())["id"] == "estoque"
    assert (d / "src/estoque/assets/demo.css").is_file()
    assert 'path = "vendor/hub_sdk-1.0.0-py3-none-any.whl"' in (d / "pyproject.toml").read_text()
    # pasta ocupada → validação
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == 2


def test_create_validation_errors(tmp_path: Path, wheel: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["project", "create", "--yes", "--dir", str(tmp_path / "x")]
    assert main(base) == 2  # sem id
    assert main([*base, "Inválido"]) == 2
    assert main([*base, "hub-x"]) == 2
    assert main([*base, "ok-app", "--stack", "cobol"]) == 2
    assert main([*base, "ok-app", "--requires", "fila"]) == 2
    assert main([*base, "ok-app", "--db", "mysql://x"]) == 2
    assert main([*base, "ok-app", "--name", "x" * 81]) == 2
    assert main(["project", "create", "--bogus"]) == 2
    assert "erro" in capsys.readouterr().err


def test_create_interactive_and_db_url(tmp_path: Path, wheel: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    answers = iter(["meu-app", "Meu App", "", "postgresql://u@h/db", "database"])
    monkeypatch.setattr("builtins.input", lambda _p: next(answers))
    monkeypatch.chdir(tmp_path)
    assert main(["project", "create"]) == 0
    assert (tmp_path / "meu-app" / ".env").read_text() == "DATABASE_URL=postgresql://u@h/db\n"
    assert json.loads((tmp_path / "meu-app/manifest.json").read_text())["name"] == "Meu App"


def test_sdk_wheel_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(project.resources, "files", lambda _p: tmp_path)
    with pytest.raises(CliError, match="SDK"):
        project.sdk_wheel()


def test_manifest_problems() -> None:
    bad = {"id": "X", "version": "1", "routes": ["/y/*"], "requires": {"database": "x"}}
    probs = check.manifest_problems(bad)
    assert len(probs) == 6 and any(p.startswith("requires") for p in probs)


def fake_app(app_id: str, good: bool = True) -> FastAPI:
    a = FastAPI()
    body = '<div class="workspace x"><a href="/apps/x/a">a</a></div>' if good else "<html><script>x</script>"

    @a.get(f"/{app_id}/_hub/ready")
    def ready() -> JSONResponse:
        return JSONResponse({"ready": True}, status_code=200 if good else 503)

    @a.get(f"/{app_id}/api/openapi.json")
    def oa() -> JSONResponse:
        op = {"operationId": "listar", "responses": {"200": {}}}
        return JSONResponse({"openapi": "3.1.0", "paths": {f"/{app_id}/api/v1/itens": {"get": op}}})

    @a.get(f"/{app_id}/api/v1/itens")
    def itens() -> JSONResponse:
        return JSONResponse([], status_code=401)

    @a.get(f"/{app_id}/{{s}}")
    @a.get(f"/{app_id}/")
    def frag(s: str = "") -> HTMLResponse:
        return HTMLResponse(body, status_code=404 if s else 200, headers={"Cache-Control": "no-store"})

    return a


@pytest.fixture
def upstream(request: pytest.FixtureRequest) -> Iterator[str]:
    port = dev.free_port()
    srv = uvicorn.Server(uvicorn.Config(fake_app("xx", request.param), port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def mk_manifest(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": "xx", "name": "X", "version": "1.0.0", "routes": ["/xx/*"], "api": {"enabled": True}}
    (d / "manifest.json").write_text(json.dumps(m))
    return d


@pytest.mark.parametrize("upstream", [True], indirect=True)
def test_conformance_green(upstream: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    assert check.conformance(mk_manifest(tmp_path / "a"), upstream, "xx") == []


@pytest.mark.parametrize("upstream", [False], indirect=True)
def test_conformance_red(upstream: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path / "data"))
    fails = check.conformance(mk_manifest(tmp_path / "a"), upstream, "xx")
    assert any("ready" in f for f in fails) and any("script" in f for f in fails)


def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, err)


def test_check_steps_with_fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    d = mk_manifest(tmp_path / "a")
    calls: list[tuple[str, ...]] = []

    def fake(_d: Path, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return done(0, '{"openapi": "3.1.0"}\n' if "openapi" in args else "ok")

    monkeypatch.setattr(check, "_uv_raw", fake)
    monkeypatch.setattr(check, "step_conformance", lambda _d: (True, "ok"))
    monkeypatch.setattr(check, "STEPS", [(n, check.step_conformance if n == "conformidade" else f)
                                         for n, f in check.STEPS])  # fmt: skip
    assert main(["check", "--dir", str(d), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["steps"][0]["step"] == "manifesto"
    assert "atualizado" in next(s for s in res["steps"] if s["step"] == "openapi")["detail"]
    assert check.step_openapi(d) == (True, "openapi.json em dia")
    monkeypatch.setattr(check, "_uv_raw", lambda _d, *a: done(1, "", "quebrou"))
    assert main(["check", "--dir", str(d), "--only", "ruff,openapi"]) == 1
    assert "FALHOU" in capsys.readouterr().out
    assert "Para corrigir: uv run ruff check --fix ." in check.run(d, ["ruff"])[0]["detail"]
    assert main(["check", "--dir", str(d), "--only", "nada"]) == 2
    assert main(["check", "--dir", str(tmp_path / "vazio")]) == 2


def test_check_step_exception_and_uv_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = mk_manifest(tmp_path / "a")

    def boom(_d: Path) -> tuple[bool, str]:
        raise RuntimeError("x")

    monkeypatch.setattr(check, "STEPS", [("boom", boom)])
    assert check.run(d)[0]["detail"] == "RuntimeError: x"

    def nouv(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(check.subprocess, "run", nouv)
    with pytest.raises(CliError, match="uv"):
        check._uv(d, "ruff")
    (d / "manifest.json").write_text("[]")
    with pytest.raises(CliError):
        check._manifest(d)


def test_step_conformance_app_never_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert check._wait("http://127.0.0.1:1/x", 0.5)[0] is False
    d = mk_manifest(tmp_path / "a")
    monkeypatch.setattr(
        dev, "app_command", lambda *_a, **_k: [sys.executable, "-c", "import sys; sys.exit(3)"]
    )
    monkeypatch.setattr(check, "_wait", lambda _u, seconds=60: (False, "HTTP 503"))
    ok, detail = check.step_conformance(d)
    assert not ok and "não ficou pronto" in detail
    monkeypatch.setattr(check, "_wait", lambda _u, seconds=60: (True, ""))
    monkeypatch.setattr(check, "conformance", lambda *_a: [])
    assert check.step_conformance(d) == (True, "ok")


def test_check_full_runs_the_complete_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """`hub check --full`: a suíte completa (platform/conformance) contra o app, com o conformance.json do app."""
    d = mk_manifest(tmp_path / "a")
    assert "conformidade-completa" not in [n for n, _ in check.STEPS]  # só com --full
    ok, detail = check.step_full_conformance(d)
    assert not ok and "conformance.json" in detail
    (d / "conformance.json").write_text(
        json.dumps({"id": "xx", "sections": ["inicio"], "durable": True, "jobs": []})
    )
    monkeypatch.setattr(
        dev, "app_command", lambda *_a, **_k: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    monkeypatch.setattr(check, "_wait", lambda _u, seconds=60: (True, ""))
    seen: dict[str, Any] = {}

    def fake(_d: Path, *args: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        seen.update(args=args, env=extra or {})
        return done(1, "FAILED test_contract.py::test_skeleton - assert 404 == 200\n1 failed, 20 passed")

    monkeypatch.setattr(check, "_uv_raw", fake)
    ok, detail = check.step_full_conformance(d)
    assert not ok and "test_skeleton" in detail
    suite = Path(seen["args"][-1])
    assert (suite / "test_contract.py").is_file() and "pytest" in seen["args"]
    assert seen["env"]["CONF_APP"] == "xx" and seen["env"]["CONF_PROFILE"] == str(
        (d / "conformance.json").resolve()
    )
    assert seen["env"]["CONF_URL"].startswith("http://127.0.0.1:") and seen["env"]["CONF_TOKEN"]
    monkeypatch.setattr(check, "step_full_conformance", lambda _d: (True, "21 passed"))
    monkeypatch.setattr(check, "STEPS", [(n, lambda _d: (True, "ok")) for n, _ in check.STEPS])
    assert main(["check", "--dir", str(d), "--full", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["steps"][-1]["step"] == "conformidade-completa"


def test_dev_project_env_and_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = mk_manifest(tmp_path / "a")
    (d / ".env").write_text("# c\nDATABASE_URL=postgresql://h/db\nAPI=1\n")
    env = dev.app_env(d, "xx", 9000)
    assert env["DATABASE_URL"] == "postgresql://h/db" and env["API"] == "1" and env["PORT"] == "9000"
    assert env["HUB_STORAGE_LOCAL_DIR"].endswith("storage")
    cmd = dev.app_command(d, "x", 9000)
    assert "--reload" in cmd and "x.app:create_app" in cmd
    with pytest.raises(CliError, match="layout"):
        dev.run_project(d, 4173, "admin")
    (d / "src" / "xx").mkdir(parents=True)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dev, "run", lambda *a: seen.update(args=a) or 0)
    assert dev.run_project(d, 4173, "admin") == 0
    assert seen["args"][1].startswith("http://127.0.0.1:") and seen["args"][6]["HUB_APP_ID"] == "xx"
    assert main(["dev", "--dir", str(tmp_path / "nada")]) == 2
    monkeypatch.setattr(dev, "run_project", lambda *_a: 0)
    assert main(["dev", "--dir", str(d)]) == 0


def test_bundled_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "_assets" / "shell").mkdir(parents=True)
    monkeypatch.setattr(dev.resources, "files", lambda _p: tmp_path)
    monkeypatch.delenv("HUB_ASSET_DIRS", raising=False)
    dev.bundled_assets()
    assert str(tmp_path / "_assets" / "shell") in __import__("os").environ["HUB_ASSET_DIRS"]


@pytest.mark.parametrize("extra", [[], ["--with", "dashboards"]])
def test_template_formatted_with_longest_id(
    tmp_path: Path, wheel: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    """O `hub check` do projeto recém-criado não reprova formatação, mesmo com o id mais longo (32)."""
    app_id = "a" + "-b" * 15 + "c"
    d = tmp_path / "p"
    args = [
        "project",
        "create",
        app_id,
        "--stack",
        "python-fastapi",
        "--dir",
        str(d),
        "--yes",
        "--json",
        *extra,
    ]
    assert main(args) == 0
    capsys.readouterr()
    for cmd in (["format", "--check"], ["check"]):
        r = subprocess.run([sys.executable, "-m", "ruff", *cmd, "."], cwd=d, capture_output=True, text=True)  # noqa: S603
        assert r.returncode == 0, r.stdout + r.stderr
