"""End-to-end flows against Postgres (``HUB_DB_TEST_URL``): sample import, upload/process/run,
roles and visibility, cancel and timeout killing the subprocess, the terminal-status rule,
triggers firing exactly once, idempotency and search."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from automations import store, triggers
from automations.seed import seed
from tests.conftest import AS_ADMIN, AS_BIA, HUB, Running

pytestmark = pytest.mark.db
API = "/automations/api/v1"
SLEEPER = "import time\ndef run(p):\n    print('dormindo')\n    time.sleep(float(p.get('s') or 30))\n    return {'ok': True}\n"


def pkg_zip(name: str, version: str, main: str = "def run(p):\n    return {'eco': p}\n") -> bytes:
    manifest = {"name": name, "version": version, "entrypoint": "main.py:run",
                "fields": [{"name": "s", "label": "Segundos", "type": "number"}]}  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("automation.json", json.dumps(manifest))
        zf.writestr("main.py", main)
    return buf.getvalue()


def idem() -> dict[str, str]:
    return {"idempotency-key": f"t-{uuid.uuid4()}"}


async def upload(r: Running, data: bytes) -> Any:
    return await r.client.post(
        f"{API}/packages", files={"arquivo": ("p.zip", data)}, headers={**AS_ADMIN, **idem()}
    )


async def make_process(
    r: Running, name: str, version: str = "1.0.0", main: str = SLEEPER, **extra: Any
) -> int:
    assert (await upload(r, pkg_zip(name, version, main))).status_code == 201
    body = {"name": name, "package_name": name, "package_version": version, **extra}
    res = await r.client.post(f"{API}/processes", json=body, headers={**AS_ADMIN, **idem()})
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


async def wait_status(r: Running, run_id: int, wanted: set[str], timeout: float = 30) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    run: dict[str, Any] = {}
    while loop.time() < end:
        run = (await r.client.get(f"{API}/runs/{run_id}", headers=AS_ADMIN)).json()
        if run["status"] in wanted:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach {wanted}: {run}")


async def process_id(r: Running, name: str) -> int:
    items = (await r.client.get(f"{API}/processes")).json()["items"]
    return next(p["id"] for p in items if p["name"] == name)


async def outbox(r: Running) -> list[dict[str, Any]]:
    return (await r.client.get("/automations/_hub/outbox?after=0&limit=500", headers=HUB)).json()["events"]


async def test_samples_are_imported_once_with_disabled_triggers(running_db: Running) -> None:
    r = running_db
    await seed(r.st.sdk)  # a second start changes nothing
    names = {p["name"] for p in (await r.client.get(f"{API}/processes")).json()["items"]}
    assert names == {"Consultar CEP", "Feriados nacionais", "Validar CPF/CNPJ"}
    pkgs = (await r.client.get(f"{API}/packages", headers=AS_ADMIN)).json()["items"]
    assert sorted((p["name"], p["version"]) for p in pkgs) == [
        ("consultar-cep", "1.0.0"), ("feriados-nacionais", "1.0.0"), ("validar-documentos", "1.0.0")]  # fmt: skip
    trig = await triggers.list_triggers(r.st.pool)
    assert len(trig) == 3 and not any(g.enabled for g in trig)


async def test_form_run_of_the_documents_sample(running_db: Running) -> None:
    r = running_db
    pid = await process_id(r, "Validar CPF/CNPJ")
    home = (await r.client.get("/automations")).text
    assert (
        f'data-hub-post="/apps/automations/catalog/{pid}/run"' in home and 'name="p_documentos"' in home
    )  # posts via hub-kit
    bad = await r.client.post(f"/automations/catalog/{pid}/run", data={"p_documentos": ""}, headers=idem())
    assert bad.status_code == 422 and "Campo obrigatório." in bad.text
    res = await r.client.post(f"/automations/catalog/{pid}/run",
                              data={"p_documentos": "529.982.247-25\n123", "p_tipo": "auto"}, headers=idem())  # fmt: skip
    assert res.status_code == 303
    run_id = int(res.headers["location"].rsplit("/", 1)[1])
    assert res.headers["location"] == f"/automations/runs/{run_id}"
    run = await wait_status(r, run_id, {"concluido", "erro"})
    assert run["status"] == "concluido", run
    assert run["output"]["validos"] == 1 and run["package_version"] == "1.0.0"
    page = (await r.client.get(f"/automations/runs/{run_id}")).text
    assert "Válidos" in page and "data-hub-poll" not in page and "Reexecutar" in page
    jobs = (await r.client.get("/automations/_hub/jobs?limit=50", headers=HUB)).json()["jobs"]
    assert any(j["kind"] == "automations.run" and j["payload"] == {"run_id": run_id} for j in jobs)
    actions = [e["action"] for e in await outbox(r)]
    assert "Execução solicitada" in actions and "Execução concluída" in actions


async def test_network_failure_of_a_sample_is_a_friendly_error(
    running_db: Running, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOMATIONS_VIACEP_URL", "http://127.0.0.1:9")  # nothing listens: no real network
    r = running_db
    pid = await process_id(r, "Consultar CEP")
    res = await r.client.post(
        f"{API}/processes/{pid}/runs", json={"params": {"cep": "01001-000"}}, headers=idem()
    )
    assert res.status_code == 202 and res.json()["trigger"] == "api"
    run = await wait_status(r, res.json()["id"], {"erro", "concluido"})
    assert run["status"] == "erro" and run["error"] and "Traceback" in (run["log"] or "")


async def test_upload_rules(running_db: Running) -> None:
    r = running_db
    assert (await upload(r, pkg_zip("eco", "1.0.0"))).status_code == 201
    dup = await upload(r, pkg_zip("eco", "1.0.0"))
    assert dup.status_code == 409 and "imutáveis" in dup.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "automation.json", json.dumps({"name": "x", "version": "1.0.0", "entrypoint": "main.py:run"})
        )
        zf.writestr("main.py", "")
        zf.writestr("../../fora.py", "x")
    slip = await upload(r, buf.getvalue())
    assert slip.status_code == 422 and any("'..'" in e for e in slip.json()["errors"])
    big = await upload(r, b"x" * (5 * 1024 * 1024 + 10))
    assert big.status_code == 422
    res = await r.client.post(
        f"{API}/packages", files={"arquivo": ("p.zip", pkg_zip("eco", "2.0.0"))}, headers=idem()
    )
    assert res.status_code == 403, "non-admin"
    ui = await r.client.post("/automations/packages", files={"arquivo": ("p.zip", pkg_zip("eco", "3.0.0"))},
                             headers={**AS_ADMIN, **idem()})  # fmt: skip
    assert ui.status_code == 303
    ui_dup = await r.client.post("/automations/packages", files={"arquivo": ("p.zip", pkg_zip("eco", "3.0.0"))},
                                 headers={**AS_ADMIN, **idem()})  # fmt: skip
    assert ui_dup.status_code == 409 and "imutáveis" in ui_dup.text
    import base64

    b64 = base64.b64encode(pkg_zip("eco", "4.0.0")).decode()
    res = await r.client.post(
        f"{API}/packages/base64", json={"content_base64": b64}, headers={**AS_ADMIN, **idem()}
    )
    assert res.status_code == 201 and res.json()["version"] == "4.0.0"


async def test_version_change_is_audited_and_runs_pin_their_version(running_db: Running) -> None:
    r = running_db
    pid = await make_process(r, "eco", main="def run(p):\n    return {'v': 1}\n")
    assert (await upload(r, pkg_zip("eco", "1.1.0", "def run(p):\n    return {'v': 2}\n"))).status_code == 201
    first = (await r.client.post(f"{API}/processes/{pid}/runs", json={}, headers=idem())).json()
    body = {"name": "eco", "package_name": "eco", "package_version": "1.1.0", "timeout_s": 60}
    res = await r.client.put(f"{API}/processes/{pid}", json=body, headers={**AS_ADMIN, **idem()})
    assert res.status_code == 200 and res.json()["package_version"] == "1.1.0"
    assert any(
        e["action"] == "Versão do processo alterada" and "1.0.0 → eco 1.1.0" in e["detail"]
        for e in await outbox(r)
    )
    second = (await r.client.post(f"{API}/processes/{pid}/runs", json={}, headers=idem())).json()
    assert (await wait_status(r, first["id"], {"concluido"}))["output"] == {"v": 1}
    assert (await wait_status(r, second["id"], {"concluido"}))["output"] == {"v": 2}
    bad = await r.client.put(
        f"{API}/processes/{pid}", json={**body, "package_version": "9.9.9"}, headers={**AS_ADMIN, **idem()}
    )
    assert bad.status_code == 422


async def test_process_form_validates_defaults_against_the_package(running_db: Running) -> None:
    r = running_db
    assert (await upload(r, pkg_zip("eco", "1.0.0"))).status_code == 201
    form = {
        "nome": "Eco",
        "pacote": "eco",
        "versao": "1.0.0",
        "tempo": "60",
        "ativo": "1",
        "parametros": '{"zz": 1}',
    }
    res = await r.client.post("/automations/processes", data=form, headers={**AS_ADMIN, **idem()})
    assert res.status_code == 422 and "Campo desconhecido neste pacote." in res.text
    res = await r.client.post(
        "/automations/processes", data={**form, "parametros": '{"s": "2"}'}, headers={**AS_ADMIN, **idem()}
    )
    assert res.status_code == 303
    page = (await r.client.get("/automations/processes/new", headers=AS_ADMIN)).text
    assert 'data-hub-depends="#pr-pacote"' in page and "eco" in page
    dup = await r.client.post(
        "/automations/processes", data={**form, "parametros": "{}"}, headers={**AS_ADMIN, **idem()}
    )
    assert dup.status_code == 409


async def test_users_see_only_their_runs(running_db: Running) -> None:
    r = running_db
    pid = await process_id(r, "Validar CPF/CNPJ")
    mine = (await r.client.post(f"{API}/processes/{pid}/runs", json={"params": {"documentos": "1"}},
                                headers=idem())).json()  # fmt: skip
    assert (await r.client.get(f"{API}/runs/{mine['id']}", headers=AS_BIA)).status_code == 404
    assert (await r.client.get(f"/automations/runs/{mine['id']}", headers=AS_BIA)).status_code == 404
    assert (await r.client.post(f"/automations/runs/{mine['id']}/cancel", headers=AS_BIA)).status_code == 404
    assert (await r.client.get(f"{API}/runs", headers=AS_BIA)).json()["total"] == 0
    assert (await r.client.get(f"{API}/runs")).json()["total"] == 1
    assert (await r.client.get(f"{API}/runs", headers=AS_ADMIN)).json()["total"] >= 1
    page = (await r.client.get("/automations/runs?periodo=7", headers=AS_BIA)).text
    assert "Nenhuma execução neste filtro." in page


async def test_disabled_process_is_hidden_from_users(running_db: Running) -> None:
    r = running_db
    pid = await make_process(r, "oculto", enabled=False)
    assert (await r.client.post(f"{API}/processes/{pid}/runs", json={}, headers=idem())).status_code == 404
    assert "oculto" not in (await r.client.get("/automations")).text
    assert (
        await r.client.post(f"{API}/processes/{pid}/runs", json={}, headers={**AS_ADMIN, **idem()})
    ).status_code == 202


async def test_cancel_kills_the_subprocess_and_the_status_never_changes_again(running_db: Running) -> None:
    r = running_db
    pid = await make_process(r, "dorminhoco")
    run = (
        await r.client.post(f"{API}/processes/{pid}/runs", json={"params": {"s": 30}}, headers=idem())
    ).json()
    await wait_status(r, run["id"], {"processando"})
    started = asyncio.get_running_loop().time()
    res = await r.client.post(f"/automations/runs/{run['id']}/cancel", headers=idem())
    assert res.status_code == 303
    final = await wait_status(r, run["id"], {"cancelado"})
    assert final["status"] == "cancelado"
    stored = await store.get_run(r.st.pool, run["id"])
    assert stored is not None and stored.job_id is not None
    job_id = stored.job_id
    for _ in range(100):  # the job ends as soon as the subprocess is killed, not after its 30 s sleep
        job = await r.st.sdk.jobs.get(job_id)
        if job.status in ("done", "failed", "cancelled", "dead"):
            break
        await asyncio.sleep(0.2)
    assert job.status in ("done", "failed", "cancelled"), job.status
    assert asyncio.get_running_loop().time() - started < 20, "subprocess killed, not slept"
    # terminal is terminal: late outcomes change nothing
    assert await store.finish_run(r.st.pool, run["id"], "concluido", {"x": 1}, None) is False
    assert await store.start_run(r.st.pool, run["id"]) is None
    final_row = await store.get_run(r.st.pool, run["id"])
    assert final_row is not None and final_row.status == "cancelado"
    again = await r.client.post(f"/automations/runs/{run['id']}/cancel", headers=idem())
    assert again.status_code == 303
    actions = [e["action"] for e in await outbox(r)]
    assert actions.count("Execução cancelada") == 1


async def test_timeout_kills_the_subprocess(running_db: Running) -> None:
    r = running_db
    pid = await make_process(r, "lento", timeout_s=1)
    run = (
        await r.client.post(f"{API}/processes/{pid}/runs", json={"params": {"s": 30}}, headers=idem())
    ).json()
    final = await wait_status(r, run["id"], {"erro", "concluido"}, timeout=25)
    assert final["status"] == "erro" and final["error"] == "tempo limite de 1 s excedido"
    assert "dormindo" in (final["log"] or "")


async def test_rerun_creates_a_new_run(running_db: Running) -> None:
    r = running_db
    pid = await process_id(r, "Validar CPF/CNPJ")
    first = (await r.client.post(f"{API}/processes/{pid}/runs", json={"params": {"documentos": "1"}},
                                 headers=idem())).json()  # fmt: skip
    res = await r.client.post(f"/automations/runs/{first['id']}/rerun", headers=idem())
    new_id = int(res.headers["location"].rsplit("/", 1)[1])
    assert res.status_code == 303 and new_id != first["id"]
    assert (await wait_status(r, new_id, {"concluido"}))["params"] == first["params"]


async def test_idempotent_submission_runs_once(running_db: Running) -> None:
    r = running_db
    pid = await process_id(r, "Validar CPF/CNPJ")
    key = idem()
    a = await r.client.post(f"/automations/catalog/{pid}/run", data={"p_documentos": "1"}, headers=key)
    b = await r.client.post(f"/automations/catalog/{pid}/run", data={"p_documentos": "1"}, headers=key)
    assert a.status_code == b.status_code == 303 and b.headers.get("idempotent-replay") == "true"
    assert (await r.client.get(f"{API}/runs")).json()["total"] == 1


async def test_trigger_fires_exactly_once_across_concurrent_schedulers(running_db: Running) -> None:
    r = running_db
    pool = r.st.pool
    pid = await process_id(r, "Validar CPF/CNPJ")
    form = {"processo": str(pid), "tipo": "intervalo", "intervalo": "5", "p_documentos": "529.982.247-25"}
    assert (
        await r.client.post("/automations/triggers", data=form, headers={**AS_ADMIN, **idem()})
    ).status_code == 303
    g = (await triggers.list_triggers(pool))[-1]
    assert not g.enabled, "a new trigger starts disabled"
    now = datetime.now(UTC)
    assert await triggers.fire_due(pool, now + timedelta(hours=1)) == [], "disabled: never fires"
    res = await r.client.post(
        f"/automations/triggers/{g.id}/enabled", data={"ativo": "1"}, headers={**AS_ADMIN, **idem()}
    )
    assert res.status_code == 303
    g = next(x for x in await triggers.list_triggers(pool) if x.id == g.id)
    assert g.enabled and g.next_run_at > now, "enabling recomputes the next fire from now"
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE aut_triggers SET next_run_at = now() - interval '1 minute' WHERE id = $1", g.id
        )
    fired = await asyncio.gather(*(triggers.fire_due(pool, datetime.now(UTC)) for _ in range(4)))
    assert sum(len(x) for x in fired) == 1, fired
    run_id = next(i for x in fired for i in x)
    run = await wait_status(r, run_id, {"concluido"})
    assert run["trigger"] == "agendamento" and run["requested_by"] == "agendamento"
    after = next(x for x in await triggers.list_triggers(pool) if x.id == g.id)
    assert after.next_run_at > datetime.now(UTC) + timedelta(minutes=4) and after.last_run_id == run_id
    page = (await r.client.get("/automations/triggers", headers=AS_ADMIN)).text
    assert "a cada 5 min" in page and f'href="/apps/automations/runs/{run_id}"' in page
    edit = (await r.client.get(f"/automations/triggers/{g.id}/edit", headers=AS_ADMIN)).text
    assert 'value="5"' in edit
    form2 = {**form, "tipo": "diario", "horario": "07:30"}
    assert (
        await r.client.post(f"/automations/triggers/{g.id}", data=form2, headers={**AS_ADMIN, **idem()})
    ).status_code == 303
    bad = await r.client.post(
        f"/automations/triggers/{g.id}", data={**form2, "horario": "x"}, headers={**AS_ADMIN, **idem()}
    )
    assert bad.status_code == 422 and "HH:MM" in bad.text
    res = await r.client.post(f"/automations/triggers/{g.id}/delete", headers={**AS_ADMIN, **idem()})
    assert res.status_code == 303 and all(x.id != g.id for x in await triggers.list_triggers(pool))
    actions = [e["action"] for e in await outbox(r)]
    assert {"Gatilho criado", "Gatilho ativado", "Gatilho alterado", "Gatilho excluído"} <= set(actions)


async def test_disabled_process_does_not_fire_its_trigger(running_db: Running) -> None:
    r = running_db
    pool = r.st.pool
    pid = await make_process(r, "parado", enabled=False)
    async with pool.acquire() as c, c.transaction():
        await c.execute(
            "INSERT INTO aut_triggers (process_id, kind, every_minutes, enabled, next_run_at, created_by) "
            "VALUES ($1, 'intervalo', 5, true, now() - interval '1 minute', 't')",
            pid,
        )
    assert await triggers.fire_due(pool, datetime.now(UTC)) == []


async def test_pages_and_search_with_data(running_db: Running) -> None:
    r = running_db
    pid = await process_id(r, "Validar CPF/CNPJ")
    run = (await r.client.post(f"{API}/processes/{pid}/runs", json={"params": {"documentos": "1"}},
                               headers=idem())).json()  # fmt: skip
    await wait_status(r, run["id"], {"concluido"})
    hist = (await r.client.get(f"/automations/runs?processo={pid}&status=concluido")).text
    assert f'data-run="{run["id"]}"' in hist
    home = (await r.client.get("/automations")).text
    assert f'href="/apps/automations/runs/{run["id"]}"' in home
    for path in ("/automations/packages", "/automations/processes", f"/automations/processes/{pid}/edit",
                 "/automations/triggers", "/automations/triggers/new"):  # fmt: skip
        assert (await r.client.get(path, headers=AS_ADMIN)).status_code == 200, path
    hits = (
        await r.client.get(
            "/automations/_hub/search?q=validar&limit=5",
            headers={**HUB, "x-hub-user": r.client.headers["x-hub-user"]},
        )
    ).json()["results"]
    assert hits and hits[0]["url"] == f"/apps/automations/runs?processo={pid}"
    hits = (
        await r.client.get(
            f"/automations/_hub/search?q=%23{run['id']}",
            headers={**HUB, "x-hub-user": r.client.headers["x-hub-user"]},
        )
    ).json()["results"]
    assert hits[0]["url"] == f"/apps/automations/runs/{run['id']}"
    others = (
        await r.client.get(f"/automations/_hub/search?q={run['id']}", headers={**HUB, **AS_BIA})
    ).json()["results"]
    assert all("/runs/" not in h["url"] for h in others)
