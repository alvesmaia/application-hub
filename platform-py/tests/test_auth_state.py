import hashlib
import os
import secrets

import pytest

from hub.auth import AuthStore, now_ms
from hub.state import Config, StateDb

DB_URL = os.getenv("HUB_DB_TEST_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")


async def test_sessions_and_rate_limits_share_state_db(tmp_path) -> None:
    assert DB_URL
    db = await StateDb.connect(Config(DB_URL, "auth_" + secrets.token_hex(4)))
    try:
        store = AuthStore(tmp_path / "auth.json", db)
        user = {
            "id": "u-test",
            "email": "a@example.test",
            "name": "A",
            "roles": [],
            "active": True,
            "created_at": 1,
        }
        async with store.locked(True) as data:
            data["users"].append(user)
        cookie = await store.session_new(user)
        assert await store.session(cookie) is not None
        assert (await db.session_get(hashlib.sha256(cookie.encode()).hexdigest())) is not None
        assert await store.logout(cookie) is None
        assert await store.session(cookie) is None
        assert (await db.rate_hit("login", "203.0.113.10:a@example.test", 900_000, 5, now_ms()))[0] == 1
    finally:
        await db.close()
