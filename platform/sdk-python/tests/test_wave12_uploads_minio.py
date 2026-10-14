"""Wave 12 (2.9): two-phase upload with Postgres (``HUB_DB_TEST_URL``) and a real disposable MinIO
(``DOCKER_AVAILABLE=1``; container ``hub-itest-upl-py``, label ``hub.ci=1``,
images ``HUB_MINIO_IMAGE``/``HUB_MC_IMAGE``): commit + promote, rolled-back transaction, crash
between the phases with orphan cleanup, pre-signed ``PUT``."""

# ruff: noqa: S603, S607 — the test controls Docker with fixed arguments
from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from hub_sdk.storage import S3Config, S3Connection, StorageError
from hub_sdk.uploads import Uploads

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.environ.get("DOCKER_AVAILABLE") != "1", reason="DOCKER_AVAILABLE≠1"),
]

NAME = "hub-itest-upl-py"
PORT = "55493"


@pytest.fixture
def minio() -> Iterator[S3Connection]:
    img = os.environ.get("HUB_MINIO_IMAGE", "hub-minio:RELEASE.2025-09-07T16-13-09Z")
    mc = os.environ.get("HUB_MC_IMAGE", "hub-mc:RELEASE.2025-08-13T08-35-41Z")
    pw = secrets.token_hex(12)
    subprocess.run(["docker", "rm", "-fv", NAME], capture_output=True, check=False)
    subprocess.run(
        ["docker", "run", "-d", "--name", NAME, "--label", "hub.ci=1", "--memory", "512m",
         "-p", f"127.0.0.1:{PORT}:9000", "-e", "MINIO_ROOT_USER=itestroot",
         "-e", f"MINIO_ROOT_PASSWORD={pw}", "-e", "MINIO_BROWSER=off", img, "server", "/data"],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        ok = False
        for _ in range(60):
            r = subprocess.run(
                ["docker", "run", "--rm", "--label", "hub.ci=1", "--network", f"container:{NAME}",
                 "-e", f"MC_HOST_m=http://itestroot:{pw}@127.0.0.1:9000", mc, "mb", "--ignore-existing", "m/docs"],
                capture_output=True, check=False,
            )  # fmt: skip
            if r.returncode == 0:
                ok = True
                break
            time.sleep(1)
        assert ok, "MinIO de teste não subiu"
        yield S3Connection(
            "docs",
            S3Config(
                endpoint=f"http://127.0.0.1:{PORT}",
                bucket="docs",
                access_key="itestroot",
                secret_key=pw,
                base_folder="app",
            ),
        )
    finally:
        subprocess.run(["docker", "rm", "-fv", NAME], capture_output=True, check=False)


async def test_two_phase_upload_with_minio(pool: Any, minio: S3Connection) -> None:
    up = Uploads(pool, minio, ttl=120)
    s = await up.begin("contrato.pdf", "ana")
    await up.put(s, b"%PDF-1.7 ok", "application/pdf")
    with pytest.raises(StorageError):
        await minio.meta("2026/contrato.pdf")
    async with pool.acquire() as c, c.transaction():
        await up.commit(c, s.id, "2026/contrato.pdf", "ana")
    assert await up.location(s.id) == s.staging_path
    await up.promote(s.id)
    await up.promote(s.id)
    assert await up.location(s.id) == "2026/contrato.pdf"
    assert (await minio.meta("2026/contrato.pdf")).size == 11
    with pytest.raises(StorageError) as e:
        await minio.meta(s.staging_path)
    assert e.value.status == 404

    s2 = await up.begin("perdido.pdf", "ana")
    await up.put(s2, b"yy", "application/pdf")
    await minio.upload("_staging/upl-semregistro/x.bin", b"z", "application/octet-stream", 1)
    rep = await up.cleanup()
    assert (rep.expired, rep.orphans) == (0, 1)
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE hub_uploads SET expires_at = now() - interval '1 second' WHERE id = $1", s2.id
        )
    assert (await up.cleanup()).expired == 1
    with pytest.raises(StorageError):
        await minio.meta(s2.staging_path)
    with pytest.raises(StorageError):
        await minio.meta("2026/perdido.pdf")

    s3 = await up.begin("direto.txt", "ana")
    assert s3.put_url is not None
    url = s3.put_url

    def put() -> int:
        req = urllib.request.Request(url, data=b"via url", method="PUT")  # noqa: S310 — local http URL of the test
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return int(r.status)

    assert 200 <= await asyncio.to_thread(put) < 300
    assert (await minio.meta(s3.staging_path)).size == 7
