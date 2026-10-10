import hashlib
import json
import tempfile
import time
from pathlib import Path

from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient

SESSION = "test-session"
_HASH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16).hash(
    "test-password"
)


def client_for(app: FastAPI) -> TestClient:
    temporary = tempfile.TemporaryDirectory(prefix="hub-auth-test-")
    app.state.auth_test_temp = temporary
    path = Path(temporary.name)
    now = time.time_ns() // 1_000_000
    session_hash = hashlib.sha256(SESSION.encode()).hexdigest()
    (path / "auth.json").write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "u-test",
                        "email": "admin@demo.local",
                        "name": "Administrador",
                        "roles": ["Administrador"],
                        "active": True,
                        "password_hash": _HASH,
                        "must_change_password": False,
                        "created_at": now,
                        "updated_at": now,
                        "last_login": None,
                        "failed_attempts": 0,
                        "locked_until": None,
                    }
                ],
                "sessions": [
                    {
                        "id_hash": session_hash,
                        "user_id": "u-test",
                        "created_at": now,
                        "last_seen": now,
                        "method": "password",
                    }
                ],
                "tokens": [],
                "next_id": 1,
            }
        ),
        encoding="utf-8",
    )
    app.state.auth.path = path / "auth.json"
    client = TestClient(app, follow_redirects=False)
    client.cookies.set("hub_session", SESSION, domain="testserver", path="/")
    client.headers["cookie"] = f"hub_session={SESSION}"
    return client
