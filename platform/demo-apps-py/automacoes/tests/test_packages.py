"""Package format (zip checks, zip-slip, limits, manifest schema), the subprocess executor (stripped
environment, timeout, cancel, results and errors) and the sample packages' own code (network faked)."""

from __future__ import annotations

import asyncio
import io
import json
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from automacoes import executor
from automacoes.package_format import MAX_FILES, MAX_ZIP_BYTES, PackageError, build_zip, extract, inspect_zip
from tests.fakes import FakeApis
from tests.samples import SAMPLES, sample_module

MANIFEST = {
    "name": "teste",
    "version": "1.0.0",
    "description": "d",
    "entrypoint": "main.py:run",
    "fields": [],
}


def make_zip(files: dict[str, str | bytes], manifest: dict[str, Any] | None = MANIFEST) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if manifest is not None:
            zf.writestr("automation.json", json.dumps(manifest))
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_a_valid_package_is_accepted() -> None:
    ins = inspect_zip(make_zip({"main.py": "def run(p):\n    return {}\n", "lib/util.py": ""}))
    assert ins.errors == [] and ins.manifest is not None and ins.manifest.name == "teste"
    assert len(ins.sha256) == 64


@pytest.mark.parametrize(
    ("name", "why"),
    [("../evil.py", "'..'"), ("/etc/x.py", "absoluto"), ("C:/x.py", "absoluto"), ("a/../../b.py", "'..'")],
)
def test_zip_slip_paths_are_refused(name: str, why: str) -> None:
    errors = inspect_zip(make_zip({"main.py": "", name: "x"})).errors
    assert any(why in e and name in e for e in errors), errors


def test_symlinks_are_refused() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("automation.json", json.dumps(MANIFEST))
        zf.writestr("main.py", "")
        link = zipfile.ZipInfo("link.py")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    assert any("links simbólicos" in e for e in inspect_zip(buf.getvalue()).errors)


def test_size_count_and_shape_limits() -> None:
    assert "5 MB" in inspect_zip(b"x" * (MAX_ZIP_BYTES + 1)).errors[0]
    many = {f"f{i}.py": "" for i in range(MAX_FILES + 1)}
    assert any("entradas" in e for e in inspect_zip(make_zip({"main.py": "", **many})).errors)
    assert inspect_zip(b"not a zip").errors == ["arquivo: não é um zip válido"]
    assert inspect_zip(b"").errors == ["arquivo: vazio"]


def test_manifest_errors_are_path_prefixed() -> None:
    bad = {**MANIFEST, "version": "1.0", "fields": [{"name": "x", "label": "X", "type": "cor"}]}
    errors = inspect_zip(make_zip({"main.py": ""}, bad)).errors
    assert any(e.startswith("version:") for e in errors), errors
    assert any(e.startswith("fields.0.type:") for e in errors), errors
    assert inspect_zip(make_zip({"main.py": ""}, None)).errors == ["automation.json: ausente na raiz do zip"]
    assert "entrypoint" in inspect_zip(make_zip({"outro.py": ""})).errors[0]
    dup = {**MANIFEST, "fields": [{"name": "a", "label": "A", "type": "text"}] * 2}
    assert any("duplicate field" in e for e in inspect_zip(make_zip({"main.py": ""}, dup)).errors)


def test_extract_never_writes_outside(tmp_path: Path) -> None:
    with pytest.raises(PackageError):
        extract(make_zip({"../fora.py": "x"}), tmp_path / "pkg")
    assert not (tmp_path / "fora.py").exists()
    extract(make_zip({"main.py": "x", "lib/a.py": "y"}), tmp_path / "ok")
    assert (tmp_path / "ok" / "lib" / "a.py").read_text() == "y"


# ---- executor (real subprocess) --------------------------------------------------------


async def no_stop() -> BaseException | None:
    return None


async def run_pkg(tmp_path: Path, main: str, params: dict[str, Any] | None = None, timeout: float = 30,
                  stop: executor.StopCheck = no_stop) -> executor.Outcome:  # fmt: skip
    data = make_zip({"main.py": main, "helper.py": "VALUE = 41\n"})
    return await executor.execute(data, "main.py:run", params or {}, timeout, tmp_path, stop)


async def test_result_log_and_sibling_imports(tmp_path: Path) -> None:
    main = "import helper\ndef run(p):\n    print('olá do pacote')\n    return {'x': p['n'] + helper.VALUE}\n"
    out = await run_pkg(tmp_path, main, {"n": 1})
    assert out.output == {"x": 42} and out.error is None
    assert "olá do pacote" in out.log, "prints go to the log, not to the result"
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == [], "the work directory is removed"


async def test_async_run_is_supported(tmp_path: Path) -> None:
    out = await run_pkg(tmp_path, "async def run(p):\n    return {'ok': True}\n")
    assert out.output == {"ok": True}


async def test_package_errors_become_friendly_messages(tmp_path: Path) -> None:
    out = await run_pkg(tmp_path, "def run(p):\n    raise LookupError('CEP não encontrado')\n")
    assert out.error == "CEP não encontrado" and "Traceback" in out.log
    out = await run_pkg(tmp_path, "def run(p):\n    return [1]\n")
    assert out.error is not None and "dict" in out.error
    out = await run_pkg(tmp_path, "import os\ndef run(p):\n    os._exit(3)\n")
    assert out.error == "o pacote terminou com o código 3"


async def test_environment_is_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_DB_URL", "postgresql://segredo")
    monkeypatch.setenv("HUB_INTERNAL_TOKEN", "segredo-interno")
    monkeypatch.setenv("QUALQUER_SEGREDO", "x")
    out = await run_pkg(tmp_path, "import os\ndef run(p):\n    return {'env': dict(os.environ)}\n")
    assert out.output is not None
    env = out.output["env"]
    assert not {"HUB_DB_URL", "HUB_INTERNAL_TOKEN", "QUALQUER_SEGREDO"} & set(env)
    assert set(env) <= {*executor.KEEP_ENV, *executor.PASS_ENV, "HOME", "TMPDIR", "TEMP", "TMP"}


@pytest.mark.skipif(sys.platform != "linux", reason="/proc and prctl are Linux-only")
async def test_package_cannot_read_the_app_environment_through_proc(tmp_path: Path) -> None:
    main = (
        "import os\n"
        "def run(p):\n"
        "    try:\n"
        "        open(f'/proc/{os.getppid()}/environ', 'rb').read()\n"
        "        return {'lido': True}\n"
        "    except OSError:\n"
        "        return {'lido': False}\n"
    )
    out = await run_pkg(tmp_path, main)
    assert out.output == {"lido": False}


async def test_timeout_kills_the_process(tmp_path: Path) -> None:
    started = asyncio.get_running_loop().time()
    out = await run_pkg(tmp_path, "import time\ndef run(p):\n    time.sleep(60)\n", timeout=1.5)
    assert out.error == "tempo limite de 1 s excedido"
    assert asyncio.get_running_loop().time() - started < 20


async def test_stop_request_kills_and_raises(tmp_path: Path) -> None:
    calls = {"n": 0}

    async def stop() -> BaseException | None:
        calls["n"] += 1
        return RuntimeError("cancelada") if calls["n"] > 2 else None

    with pytest.raises(RuntimeError, match="cancelada"):
        await run_pkg(tmp_path, "import time\ndef run(p):\n    time.sleep(60)\n", stop=stop)


def test_sandbox_protocol_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The child's ``main`` (it only runs in subprocesses above, out of coverage's reach)."""
    from automacoes import sandbox

    (tmp_path / "main.py").write_text("def run(p):\n    print('log')\n    return {'dobro': p['n'] * 2}\n")
    (tmp_path / "ruim.py").write_text("def run(p):\n    raise ValueError('falhou')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    out = io.StringIO()
    assert sandbox.main(["main.py:run"], io.StringIO('{"n": 21}'), out) == 0
    assert json.loads(out.getvalue()) == {"ok": {"dobro": 42}}
    out = io.StringIO()
    assert sandbox.main(["ruim.py:run"], io.StringIO(""), out) == 1
    assert json.loads(out.getvalue()) == {"error": "falhou"}


def test_outcome_of_invalid_output() -> None:
    assert executor.outcome_of(0, b"nada", "").error == "o pacote não devolveu um resultado JSON válido"


# ---- sample packages -------------------------------------------------------------------


def test_sample_packages_zip_and_pass_the_checks() -> None:
    for folder in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        ins = inspect_zip(build_zip(folder))
        assert ins.errors == [], (folder.name, ins.errors)


def test_sample_cep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMACOES_VIACEP_URL", "https://viacep.test/")
    run = sample_module("consultar-cep", "main").run
    apis = FakeApis()
    out = run({"cep": "01001-000"}, transport=apis.transport())
    assert out["localidade"] == "São Paulo" and "siafi" not in out
    assert apis.calls == ["https://viacep.test/ws/01001000/json/"]
    with pytest.raises(LookupError, match="CEP não encontrado"):
        run({"cep": "99999999"}, transport=apis.transport())
    apis.unavailable = 1
    with pytest.raises(RuntimeError, match="503"):
        run({"cep": "01001000"}, transport=apis.transport())


def test_sample_holidays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMACOES_BRASILAPI_URL", "https://brasilapi.test")
    run = sample_module("feriados-nacionais", "main").run
    out = run({"ano": 2026}, transport=FakeApis().transport())
    assert out["quantidade"] == 2 and out["feriados"][1] == {"data": "2026-04-21", "nome": "Tiradentes"}
    with pytest.raises(LookupError):
        run({"ano": 1901}, transport=FakeApis().transport())


def test_sample_documents_offline() -> None:
    out = sample_module("validar-documentos", "main").run(
        {"documentos": "529.982.247-25\nabc", "tipo": "auto"}
    )
    assert (out["validos"], out["invalidos"]) == (1, 1)


async def test_sample_documents_runs_in_the_sandbox(tmp_path: Path) -> None:
    data = build_zip(SAMPLES / "validar-documentos")
    out = await executor.execute(
        data, "main.py:run", {"documentos": "11.222.333/0001-81"}, 30, tmp_path, no_stop
    )
    assert out.output is not None and out.output["validos"] == 1, out.log
