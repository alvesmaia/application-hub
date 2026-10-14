import asyncio
import json

import pytest

from hub.admin.chain import AuditChain, entry_hash
from hub.admin.store import AdminStore, LogBus, OutboxIngest
from hub.vault import Vault


def test_legacy_entry_format_and_append(tmp_path):
    event = {
        "id": 1,
        "at": 1700000000000,
        "actor": "admin",
        "icon": "users",
        "title": "Login",
        "detail": "via 127.0.0.1",
        "scope": "platform",
    }
    encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    digest = entry_hash(1, "0" * 64, encoded)
    assert digest == "0363ae08db567dbd184dd5a567075a71a10456df2365288d554675c8497f6d0a"
    chain_file = tmp_path / "audit" / "chain.jsonl"
    chain_file.parent.mkdir()
    chain_file.write_text(
        json.dumps(
            {"t": "e", "seq": 1, "prev": "0" * 64, "event": encoded, "hash": digest}, separators=(",", ":")
        )
        + "\n"
    )
    (chain_file.parent / "head.json").write_text(
        json.dumps({"seq": 1, "hash": digest, "event_id": 1, "pk": "", "sig": ""})
    )
    chain = AuditChain(chain_file.parent)
    assert chain.verify().ok
    chain.append({**event, "id": 2})
    assert chain.verify().last_seq == 2


async def test_outbox_ack_state_and_log_bus(tmp_path):
    outbox = OutboxIngest(tmp_path / "outbox")
    await outbox.recorded("app", [("event-1", 4)], 1)
    assert await outbox.pending("app") == ["event-1"]
    await outbox.acked("app", ["event-1"])
    assert await outbox.pending("app") == []
    bus = LogBus()
    bus.push({"app": "platform", "message": "real event"})
    assert bus.recent(app="platform")[0]["message"] == "real event"


def test_chain_rejects_corruption_and_store_round_trips(tmp_path):
    store = AdminStore(tmp_path)
    assert store._read()["env"] == [] and store._read()["roles"][0]["name"] == "Administrador"

    async def exercise():
        event = await store.audit("admin", "Created", "one")
        assert event["id"] == 1
        await store.env_put("PUBLIC_URL", "https://hub.test", "publica")
        with pytest.raises(ValueError):
            await store.env_put("bad-name", "value")
        with pytest.raises(ValueError):
            await store.env_put("SECRET", "value")

    asyncio.run(exercise())
    assert store.chain.verify().ok
    chain = AuditChain(tmp_path / "audit")
    line = json.loads(chain.path.read_text().splitlines()[0])
    line["hash"] = "f" * 64
    chain.path.write_text(json.dumps(line) + "\n")
    assert not chain.verify().ok
    with pytest.raises(RuntimeError, match="blocked"):
        chain.append(
            {"id": 2, "at": 1, "actor": "a", "icon": "x", "title": "t", "detail": "d", "scope": "platform"}
        )


@pytest.mark.asyncio
async def test_outbox_validation_and_log_subscribers(tmp_path):
    outbox = OutboxIngest(tmp_path / "outbox")
    await outbox.recorded("app", [("", 1), ("a" * 81, 2), ("event", 3)], 3)
    assert await outbox.pending("app") == ["event"]
    with pytest.raises(ValueError):
        await outbox.acked("../bad", [])
    bus = LogBus()
    stream = bus.subscribe()
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    bus.push({"message": "wake"})
    assert await pending == {"message": "wake"}
    await stream.aclose()


def test_chain_writes_signed_checkpoints_every_100_events(tmp_path):
    """F7a: the audit export carries signed checkpoints (`"t":"c"`)."""
    (tmp_path / "vault").mkdir()
    (tmp_path / "master.key").write_text("00" * 32, encoding="ascii")
    vault = Vault.open(tmp_path / "vault", tmp_path / "master.key")
    chain = AuditChain(tmp_path / "audit", vault)
    for i in range(1, 251):
        chain.append(
            {"id": i, "at": i, "actor": "a", "icon": "x", "title": "t", "detail": "d", "scope": "platform"}
        )
    cps = [x for x in chain._lines() if x["t"] == "c"]
    assert [c["seq"] for c in cps] == [100, 200]
    assert chain.verify().ok and chain.report()["checkpoints"] == 2
    assert AuditChain(tmp_path / "audit", vault).verify().ok  # reopening neither duplicates nor breaks it
    assert '"t":"c"' in chain.export()
