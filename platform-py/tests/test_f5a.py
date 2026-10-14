"""F5a: outbox -> Inbox/audit, backup scheduler, external copy, restore with a side database, alerts,
durable jobs and leadership across replicas (disposable Postgres in `HUB_DB_TEST_URL`)."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient
from test_ops import JSON, FakeDocker, wait_job

from hub.connections.provision import ObjectStore
from hub.ops.jobs import Jobs
from hub.ops.leader import Leader
from hub.ops.model import OpsError, now_ms
from hub.ops.outbox import allowed_roles, item_from_notice, valid_event_id
from hub.ops.schedule import Cron, jitter_ms, plan, timezone_of
from hub.state import Config, StateDb
from hub.ux.inbox import InboxStore, item_of

DB_URL = os.getenv("HUB_DB_TEST_URL")
TOKEN = "t" * 20


# ---- cron and schedule plan ----
def test_cron_next_and_plan() -> None:
    tz = timezone_of("America/Sao_Paulo")
    t0 = int(time.mktime((2026, 9, 27, 12, 0, 0, 0, 0, 0)) * 1000)
    nxt = Cron("0 2 * * *").next_after(t0, tz)
    assert nxt and nxt > t0 and (nxt - t0) < 86_400_000 + 3_600_000
    q = Cron("*/15 * * * *").next_after(t0, tz)
    assert q is not None and q - t0 <= 15 * 60_000
    sab = int(
        time.mktime((2026, 10, 3, 10, 0, 0, 0, 0, 0)) * 1000
    )  # Saturday: the next "Mon-Fri 9h" is Monday
    seg = Cron("0 9 * * 1-5").next_after(sab, tz)
    assert seg is not None and 36 * 3600_000 < seg - sab < 72 * 3600_000
    with pytest.raises(OpsError):
        Cron("* * *")
    assert jitter_ms("a", "producao", 5, 0) == 0 and 0 <= jitter_ms("a", "producao", 5, 20) <= 20_000
    assert jitter_ms("a", "producao", 5, 20) == jitter_ms("a", "producao", 5, 20)
    c, mn = Cron("*/10 * * * *"), 60_000
    s1, d, *_ = plan(None, c, tz, t0, 5 * mn, "executar", lambda _: 0)
    assert d == "wait" and s1["next_due"] > t0
    s2, d, slot, missed = plan(s1, c, tz, s1["run_at"] + mn, 5 * mn, "executar", lambda _: 0)
    assert (d, slot, missed) == ("run", s1["next_due"], False) and s2["next_due"] > s1["next_due"]
    _, d, _, missed = plan(s1, c, tz, s1["run_at"] + 20 * mn, 5 * mn, "executar", lambda _: 0)
    assert (d, missed) == ("run", True)
    s4, d, _, _ = plan(s1, c, tz, s1["run_at"] + 20 * mn, 5 * mn, "pular", lambda _: 0)
    assert d == "skip" and s4["last_status"] == "Perdido (pulado)"
    assert plan(s1, c, tz, s1["run_at"] - 1, 5 * mn, "executar", lambda _: 0)[1] == "wait"


def test_notice_units() -> None:
    assert (
        valid_event_id("evt-1")
        and not valid_event_id("")
        and not valid_event_id("a b")
        and not valid_event_id("x" * 81)
    )
    assert allowed_roles(["Suporte", "Aprovador"], [], ["suporte"]) == ["Aprovador"]
    assert allowed_roles(["Suporte", "Aprovador"], ["Suporte"], ["suporte"]) == ["Suporte"]
    ev = {"event_id": "e", "kind": "approval_request", "user": "ana", "at": 5,
          "meta": {"title": "Aprovar?", "approval_id": "ap-1", "to_roles": ["Suporte"], "link": "/ativos/x"}}  # fmt: skip
    assert item_from_notice("ativos", ev, []) is None  # refused: never falls back to "who caused it"
    it = item_from_notice("ativos", ev, ["Suporte"])
    assert (
        it and it["kind"] == "approval" and it["approval"]["ext_id"] == "ap-1" and it["source"] == "ativos:e"
    )
    assert it["link"] == "/apps/ativos/x"
    bad = item_from_notice("ativos", {**ev, "meta": {"approval_id": "x y", "title": ""}}, [])
    assert bad and bad["kind"] == "notification" and bad["title"] == "Notificação"


# ---- test hub with background tasks under control ----
class Remote:
    """Fake app upstream: outbox, ack and tasks."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.acks: list[list[str]] = []
        self.fail_ack = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/ativos/_hub/outbox":
            return httpx.Response(200, json={"events": self.events})
        if p == "/ativos/_hub/outbox/ack":
            if self.fail_ack:
                return httpx.Response(503)
            self.acks.append(json.loads(req.content)["ids"])
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def remote() -> Remote:
    return Remote()


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: Remote) -> Iterator[TestClient]:
    monkeypatch.setenv("HUB_BACKUP_SCHEDULER", "0")
    monkeypatch.setenv("HUB_OUTBOX_POLL_S", "3600")
    monkeypatch.delenv("HUB_BACKUP_OFFSITE_URL", raising=False)
    with make_hub(tmp_path, monkeypatch, remote) as c:
        c.app.state.admin.tokens["ativos"] = TOKEN  # type: ignore[attr-defined]
        yield c


def call(c: TestClient, fn: Any, *a: Any) -> Any:
    assert c.portal is not None
    return c.portal.call(fn, *a)


def audit_rows(c: TestClient) -> list[dict[str, Any]]:
    return c.get("/api/admin/audit?limit=100").json()  # type: ignore[no-any-return]


def test_outbox_exactly_once_and_inbox(hub: TestClient, remote: Remote) -> None:
    ob = hub.app.state.ops_outbox  # type: ignore[attr-defined]
    inbox = hub.app.state.inbox  # type: ignore[attr-defined]
    remote.events = [
        {"id": 1, "event_id": "e1", "kind": "audit", "user": "Ana", "action": "Ativo cadastrado", "target": "T1", "detail": "ok"},
        {"id": 2, "event_id": "e2", "kind": "notification", "user": "Ana", "meta": {"title": "Olá", "to_users": ["ana@demo.local"], "level": "success"}},
        {"id": 3, "event_id": "e3", "kind": "approval_request", "user": "Ana", "meta": {"title": "Aprovar", "approval_id": "ap-9", "to_users": ["ana@demo.local"]}},
        {"id": 4, "event_id": "e 4", "kind": "audit", "action": "inválido"},
        {"id": 5, "event_id": "e5", "kind": "desconhecido"},
    ]  # fmt: skip
    remote.fail_ack = True
    out = call(hub, ob.pull_app, "ativos", "http://upstream", TOKEN, 0)
    assert out["fetched"] == 5 and out["recorded"] == 1 and remote.acks == []  # ack failed: nothing confirmed
    titles = [e["title"] for e in audit_rows(hub)]
    assert titles.count("Ativo cadastrado") == 1
    assert {i["kind"] for i in inbox.items.values()} == {"notification", "approval"}
    assert any(i["approval"] and i["approval"]["ext_id"] == "ap-9" for i in inbox.items.values())
    # redelivered after the ack failure: nothing duplicates; now confirms everything written (except the invalid id)
    remote.fail_ack = False
    out = call(hub, ob.pull_app, "ativos", "http://upstream", TOKEN, 0)
    assert out["recorded"] == 0 and remote.acks == [["e1", "e2", "e3", "e5"]]
    assert [e["title"] for e in audit_rows(hub)].count("Ativo cadastrado") == 1 and len(inbox.items) == 2
    assert call(hub, ob.ingest.pending, "ativos") == []  # compactado
    # tick walks the apps with a token; the others answer 404 and become a single warning
    call(hub, ob.tick)
    call(hub, ob.tick)
    warns = [ln for ln in hub.app.state.admin.logs.recent() if "outbox indisponível" in ln["message"]]  # type: ignore[attr-defined]
    assert len(warns) == 0 or len({w["app"] for w in warns}) == len(warns)


def test_outbox_only_leader_acks(hub: TestClient, remote: Remote) -> None:
    ob = hub.app.state.ops_outbox  # type: ignore[attr-defined]
    remote.events = [{"id": 1, "event_id": "x1", "kind": "audit", "user": "A", "action": "Algo"}]

    async def lost() -> bool:
        return False

    ob.leader.checked = lost
    call(hub, ob.pull_app, "ativos", "http://upstream", TOKEN, 0)
    assert remote.acks == [] and call(hub, ob.ingest.pending, "ativos") == ["x1"]


# ---- agendador ----------------------------------------------------------------------------------------------
@pytest.fixture
def bk(hub: TestClient) -> FakeDocker:
    fake = FakeDocker()
    hub.app.state.ops_engine.factory = lambda: fake  # type: ignore[attr-defined]
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]

    async def env(app: str, e: str) -> dict[str, str]:
        return {"HUB_DB_URL": "postgres://u:p@db:5432/appdb"}

    b.app_env = env
    return fake


def set_policy(c: TestClient, **kw: Any) -> None:
    pol = {"schedule": "A cada 10 min", "keep": 7, "volumes": ["data"], "hooks": False, "destination": "Local",
           "jitter_s": 0, **kw}  # fmt: skip
    assert c.put("/api/ops/backups/policies/ativos", json=pol).status_code == 200


def test_scheduler_runs_skips_and_defers(hub: TestClient, bk: FakeDocker) -> None:
    sched, ops = hub.app.state.ops_scheduler, hub.app.state.ops  # type: ignore[attr-defined]
    set_policy(hub)
    t0 = now_ms()
    assert call(hub, sched.tick, t0) == []
    st = ops.read()["schedules"]["ativos|producao"]
    assert st["next_due"] > t0 and "próximo" in hub.get("/admin/backups").text
    # right away: runs as "Scheduled" with the scheduler's actor
    lines = call(hub, sched.tick, st["run_at"] + 1000)
    assert len(lines) == 1 and "Agendado" in lines[0]
    job_id = ops.read()["schedules"]["ativos|producao"]["last_job"]
    assert wait_job(hub, job_id)["ok"]
    hist = hub.get("/api/ops/backups").json()["history"]
    assert hist[0]["kind"] == "Agendado" and hist[0]["status"] == "Concluído"
    call(hub, sched.tick, st["run_at"] + 2000)
    assert ops.read()["schedules"]["ativos|producao"]["last_status"] == "Concluído"
    assert "último: Concluído" in hub.get("/admin/backups").text
    # missed (hub offline) with the "skip" policy: does not run
    set_policy(hub, missed="pular")  # policy changed: the next run is recalculated
    assert "ativos|producao" not in ops.read()["schedules"]
    call(hub, sched.tick, now_ms())
    s = ops.read()["schedules"]["ativos|producao"]

    def ativos_hist() -> int:
        return len([h for h in hub.get("/api/ops/backups").json()["history"] if h["app"] == "ativos"])

    n = ativos_hist()
    lines = call(hub, sched.tick, s["run_at"] + 3_600_000)  # +1 h may cross another app's daily schedule
    assert any(x.startswith("ativos|producao: pulado") for x in lines)
    assert ativos_hist() == n
    assert ops.read()["schedules"]["ativos|producao"]["last_status"] == "Perdido (pulado)"
    # app job in progress: postpones 15 s
    s = ops.read()["schedules"]["ativos|producao"]
    call(hub, hub.app.state.ops_jobs.claim, "ativos")  # type: ignore[attr-defined]
    assert call(hub, sched.tick, s["run_at"] + 1)[0].endswith("adiado")
    assert ops.read()["schedules"]["ativos|producao"]["last_status"] == "Adiado (job em andamento)"
    # invalid schedule becomes a visible error; manual does not schedule
    d = ops.read()
    d["policies"][next(i for i, p in enumerate(d["policies"]) if p["app"] == "ativos")]["schedule"] = "x y"
    ops.write(d)
    call(hub, sched.tick, now_ms())
    assert ops.read()["schedules"]["ativos|producao"]["error"]
    assert "agenda inválida" in hub.get("/admin/backups").text


# ---- external copy, safety snapshot, side database ----
def _read(p: str) -> bytes:
    return Path(p).read_bytes()


def _write(p: str, b: bytes) -> None:
    Path(p).write_bytes(b)


def fake_mc(store: dict[str, bytes], fail: bool = False) -> Any:
    async def runner(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        if fail:
            return 1, "mc: erro de rede"
        cp = args[args.index("cp") + 1 :]
        src, dst = cp
        if dst.startswith("off/"):
            store[dst] = _read(src)
        else:
            _write(dst, store[src])
        return 0, ""

    return runner


def test_offsite_copy_and_failure_alert(
    hub: TestClient, bk: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_BACKUP_OFFSITE_URL", "http://k:s3cr3t@app-hub-objstore:9000/bk-ext/pre")
    b, copies = hub.app.state.ops_backups, {}  # type: ignore[attr-defined]
    b.objstore = ObjectStore(runner=fake_mc(copies))
    j = wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])
    assert j["ok"]
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert "cópia externa em app-hub-objstore/bk-ext/pre/ativos/" in rec["detail"] and len(copies) == 1
    assert "s3cr3t" not in rec["detail"]
    b.objstore = ObjectStore(runner=fake_mc(copies, fail=True))
    j = wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])
    assert j["ok"]  # the local backup is the one that counts
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert rec["status"] == "Concluído" and "cópia externa FALHOU" in rec["detail"]
    alerts = [i for i in hub.app.state.inbox.items.values() if i["kind"] == "alert"]  # type: ignore[attr-defined]
    assert alerts and alerts[0]["to_perm"] == "editar_configuracoes" and alerts[0]["level"] == "error"
    monkeypatch.setenv("HUB_BACKUP_OFFSITE_URL", "ftp://x")
    with pytest.raises(OpsError):
        call(hub, b.offsite, "ativos", "producao", rec["file"])


def test_aside_restore_flow_and_undo(hub: TestClient, bk: FakeDocker) -> None:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    calls: list[tuple[str, ...]] = []
    loads: list[bool] = []

    async def psql(db: str, *stmts: str) -> str:
        calls.append(stmts)
        return "hub:aside=true\nhub:owner=appowner\n" if any("hub:aside" in x for x in stmts) else ""

    async def load(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        loads.append(single)
        return 0, b""

    b._psql, b._load = psql, load
    msg = call(hub, b._restore_db, "appdb", b"-- sql", False)
    assert "restaurado" in msg and loads == [False]  # not transactional
    assert "RENAME TO" in calls[0][0] and 'CREATE DATABASE "appdb" OWNER "appowner"' in calls[1][0]
    assert 'DROP DATABASE IF EXISTS "appdb_pre' in calls[-1][0]  # the side one only disappears at the end
    # failure midway: deletes the partial and returns the original to the name
    calls.clear()

    async def boom(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        return 1, b"ERRO sql"

    b._load = boom
    with pytest.raises(OpsError, match="banco original devolvido"):
        call(hub, b._restore_db, "appdb", b"-- sql", False)
    undo = calls[-1]
    assert undo[0] == 'DROP DATABASE IF EXISTS "appdb" WITH (FORCE)' and 'RENAME TO "appdb"' in undo[1]
    # failure before knowing whether the rename happened: nothing to undo
    calls.clear()

    async def first_fails(db: str, *stmts: str) -> str:
        calls.append(stmts)
        raise OpsError("conexão perdida")

    b._psql = first_fails
    with pytest.raises(OpsError):
        call(hub, b._restore_db, "appdb", b"-- sql", False)
    assert len(calls) == 1
    with pytest.raises(OpsError):
        call(hub, b._restore_db, "postgres", b"x", False)
    b._load = load
    assert "temporário" in call(hub, b._restore_db, "appdb", b"x", True)  # temporary database (test)


def test_aside_list_and_actions(hub: TestClient, bk: FakeDocker) -> None:
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    seen: list[str] = []

    async def psql(db: str, *stmts: str) -> str:
        seen.extend(stmts)
        if "pg_database WHERE NOT" in stmts[0]:
            return "hub:db=appdb\nhub:db=appdb_pre123\nhub:db=appdb_preX\nhub:db=outro\nhub:db=gone_pre9\n"
        return ""

    b._psql = psql
    got = hub.get("/api/ops/backups/aside").json()
    assert got["available"] and [(a["aside"], a["db"], a["db_exists"]) for a in got["aside"]] == [
        ("appdb_pre123", "appdb", True)
    ]
    # the original database exists: returning it to the name is refused; dropping it works and is audited
    assert hub.post("/api/ops/backups/aside/appdb_pre123/rename-back", headers=JSON).status_code == 409
    assert hub.post("/api/ops/backups/aside/appdb_pre123/drop", headers=JSON).status_code == 200
    assert 'DROP DATABASE IF EXISTS "appdb_pre123" WITH (FORCE)' in seen
    assert "Banco de lado" in [e["title"] for e in audit_rows(hub)]
    assert hub.post("/api/ops/backups/aside/zzz/drop", headers=JSON).status_code == 404

    async def no_original(db: str, *stmts: str) -> str:
        seen.extend(stmts)
        return "hub:db=appdb_pre123\n" if "pg_database WHERE NOT" in stmts[0] else ""

    b._psql = no_original
    assert hub.post("/api/ops/backups/aside/appdb_pre123/rename-back", headers=JSON).status_code == 200
    assert 'ALTER DATABASE "appdb_pre123" RENAME TO "appdb"' in seen


def test_restore_snapshot_maintenance_and_alert(hub: TestClient, bk: FakeDocker) -> None:
    b, ops = hub.app.state.ops_backups, hub.app.state.ops  # type: ignore[attr-defined]
    rid = wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])["result"][
        "record"
    ]

    async def load(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        return 0, b""

    async def psql(db: str, *stmts: str) -> str:
        return "hub:aside=false\n"

    b._load, b._psql = load, psql
    assert wait_job(hub, hub.post(f"/api/ops/backups/records/{rid}/restore", headers=JSON).json()["id"])["ok"]
    hist = hub.get("/api/ops/backups").json()["history"]
    snap = next(h for h in hist if h["kind"] == "Pré-restauração")
    assert snap["status"] == "Concluído" and snap["file"] and not ops.read()["maintenance"]

    # restore that fails: the app stays in maintenance (failed) and the administration is notified
    async def boom(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        return 1, b"ERRO"

    b._load = boom
    j = wait_job(hub, hub.post(f"/api/ops/backups/records/{rid}/restore", headers=JSON).json()["id"])
    assert not j["ok"]
    assert ops.read()["maintenance"]["ativos|producao"]["kind"] == "restauracao_falhou"
    assert "Liberar (dados conferidos)" in hub.get("/admin/backups").text
    assert any(
        i["kind"] == "alert" and "Restauração" in i["title"] for i in hub.app.state.inbox.items.values()
    )  # type: ignore[attr-defined]
    assert hub.post("/api/ops/maintenance/ativos/clear", headers=JSON).status_code == 200


def test_restore_safety_snapshot_can_be_disabled(
    hub: TestClient, bk: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_RESTORE_SAFETY_SNAPSHOT", "0")
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    rid = wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])["result"][
        "record"
    ]

    async def load(db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        return 0, b""

    b._load = load
    assert wait_job(hub, hub.post(f"/api/ops/backups/records/{rid}/restore", headers=JSON).json()["id"])["ok"]
    assert all(h["kind"] != "Pré-restauração" for h in hub.get("/api/ops/backups").json()["history"])


def test_inbox_reload_between_replicas_and_leader_route(hub: TestClient) -> None:
    inbox = hub.app.state.inbox  # type: ignore[attr-defined]
    path = inbox.path
    other = InboxStore(path)  # "other replica" on the same file
    other.push(item_of("alert", "ativos", "Outra réplica", source="r2"))
    woke = []
    inbox.watchers.add(type("Q", (), {"empty": lambda s: True, "put_nowait": lambda s, v: woke.append(1)})())
    assert (
        inbox.reload_if_changed()
        and woke
        and any(i["title"] == "Outra réplica" for i in inbox.items.values())
    )
    assert not inbox.reload_if_changed()
    info = hub.get("/api/ops/replicas").json()
    assert info["leader"] is True and info["shared"] is False


# ---- with Postgres: durable jobs and leadership ----
pg = pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")


@pytest.fixture
async def states() -> Any:
    assert DB_URL
    schema = f"t_{secrets.token_hex(6)}"
    a = await StateDb.connect(Config(DB_URL, schema, 8, "rep-a"))
    b = await StateDb.connect(Config(DB_URL, schema, 8, "rep-b"))
    try:
        yield a, b
    finally:
        await a.close()
        await b.close()
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()


@pg
async def test_jobs_shared_across_replicas(states: Any) -> None:
    a, b = states
    ja, jb = Jobs(a), Jobs(b)
    job = await ja.create("backup", "Backup de x", [], "x", "producao")
    assert (await jb.aget(job["id"]) or {})["title"] == "Backup de x"
    assert jb.get(job["id"]) is None and await jb.running_all() == 1
    ja.update(job["id"], lambda j: j.update(progress=50, message="metade"))
    await ja.flush()
    assert (await jb.aget(job["id"]) or {})["progress"] == 50
    with pytest.raises(OpsError):  # one backup/restore per app, also across replicas
        await jb.claim("x")
    await jb.claim("y")
    ids = {(await Jobs(a).create("deploy", "d"))["id"] for _ in range(3)}
    assert len(ids) == 3 and job["id"] not in ids  # ids from the database sequence
    # owner with no sign of life: the job becomes "interrupted"; with a sign of life, it stays
    await b.heartbeat({})
    await a.heartbeat({})
    assert await jb.interrupt_dead() == 0
    await asyncio.sleep(0)
    async with a.pool.acquire() as c:
        await c.execute("UPDATE replicas SET heartbeat_at = 0 WHERE id = 'rep-a'")
    n = await jb.interrupt_dead()
    assert n >= 1
    done = await jb.aget(job["id"])
    assert done and done["done"] and not done["ok"] and "Interrompido" in done["message"]
    ja.update(job["id"], lambda j: j.update(done=True, ok=True))
    ja.start()
    await asyncio.sleep(0.7)
    assert (await jb.aget(job["id"]) or {})["ok"] is True  # the loop writes the change without asking
    await ja.stop()


@pg
async def test_two_replicas_one_leader(states: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_LEADER_TTL_S", "3")
    a, b = states
    took: list[str] = []
    msgs: list[str] = []

    def hook(name: str) -> Any:
        async def f() -> None:
            took.append(name)

        return f

    async def nop() -> None:
        return None

    la, lb = Leader(a, lambda lvl, m: msgs.append(m)), Leader(b, lambda lvl, m: msgs.append(m))
    la.start(hook("a"), nop)
    lb.start(hook("b"), nop)
    for _ in range(100):
        if la.leading() or lb.leading():
            break
        await asyncio.sleep(0.1)
    assert la.leading() != lb.leading()
    first, second = (la, lb) if la.leading() else (lb, la)
    # the same task "of the leader" in two replicas: only the leader runs it
    ran = {"a": 0, "b": 0}
    for _ in range(5):
        for name, ld in (("a", la), ("b", lb)):
            if await ld.checked():
                ran[name] += 1
        await asyncio.sleep(0.05)
    assert sorted(ran.values()) == [0, 5]
    info = await first.info()
    assert info["leader"] and info["holder"] == first.replica and len(info["replicas"]) == 2
    assert not (await second.info())["leader"]
    assert len(took) == 1 and any("assumiu a liderança" in m for m in msgs)
    # the leader falls (without releasing the lease): the other takes over after the TTL
    assert first.lease
    for t in first.tasks:
        t.cancel()
    await first.lease.abandon()
    for _ in range(100):
        if second.leading():
            break
        await asyncio.sleep(0.1)
    for _ in range(30):  # the watchdog (0.5 s) calls the takeover hook
        if len(took) == 2:
            break
        await asyncio.sleep(0.1)
    assert second.leading() and not await first.checked() and len(took) == 2
    await second.stop()
    await first.stop()
    assert (
        Leader(None).leading()
        and await Leader(None).checked()
        and (await Leader(None).info())["shared"] is False
    )


def test_offsite_copy_to_a_sharepoint_connection(
    hub: TestClient, bk: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hub.storage import Body, Item

    files: dict[str, bytes] = {}

    class FakeGraph:
        async def upload(self, rel: str, length: int, content_type: str, body: Body) -> Item:
            files[rel] = await body.read(length)
            return Item(path=rel, name=rel.rsplit("/", 1)[-1], size=length)

        async def download(self, rel: str) -> tuple[str, int | None, Any]:
            async def it() -> Any:
                yield files[rel]

            return "application/zip", len(files[rel]), it()

    conns = {
        "bk-sp": {"name": "bk-sp", "kind": "sharepoint", "rev": 1},
        "s3": {"name": "s3", "kind": "s3", "rev": 1},
    }
    b = hub.app.state.ops_backups  # type: ignore[attr-defined]
    b.conns = type(
        "C", (), {"get": staticmethod(conns.get), "backend": staticmethod(lambda c: FakeGraph())}
    )()
    monkeypatch.setenv("HUB_BACKUP_OFFSITE_URL", "sharepoint://bk-sp/Backups/hub")
    j = wait_job(hub, hub.post("/api/ops/backups/ativos/run", headers=JSON).json()["id"])
    assert j["ok"]
    rec = hub.get("/api/ops/backups").json()["history"][0]
    assert f"cópia externa em SharePoint bk-sp/Backups/hub/ativos/{rec['file']}" in rec["detail"]
    assert files[f"Backups/hub/ativos/{rec['file']}"] == (b.root / "ativos" / rec["file"]).read_bytes()
    files_before = dict(files)
    FakeGraph.download = lambda self, rel: _corrupt(files_before[rel])  # type: ignore[method-assign]
    with pytest.raises(OpsError, match="diverge"):
        call(hub, b.offsite, "ativos", "producao", rec["file"])
    for bad in ("sharepoint://nao-existe", "sharepoint://s3"):
        monkeypatch.setenv("HUB_BACKUP_OFFSITE_URL", bad)
        with pytest.raises(OpsError, match="SharePoint"):
            call(hub, b.offsite, "ativos", "producao", rec["file"])


async def _corrupt(data: bytes) -> tuple[str, int | None, Any]:
    async def it() -> Any:
        yield data + b"x"

    return "application/zip", len(data) + 1, it()
