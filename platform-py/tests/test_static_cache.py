"""Navigation cost: the English catalog (≈166 KB, on every hub screen) answers 304 when unchanged, and text assets
travel compressed when the browser accepts gzip (hub.css ≈108 KB → ≈24 KB)."""

from __future__ import annotations

import gzip

import pytest
from auth_helper import client_for
from fastapi.testclient import TestClient

from hub.app import create_app


@pytest.fixture
def client() -> TestClient:
    return client_for(create_app())


def test_the_english_catalog_revalidates_with_an_etag(client: TestClient) -> None:
    r = client.get("/assets/i18n/en.json")
    assert r.status_code == 200 and r.headers["etag"] and r.json()["lang"] == "en"
    again = client.get("/assets/i18n/en.json", headers={"if-none-match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_text_assets_are_gzipped_when_accepted(client: TestClient) -> None:
    plain = client.get("/assets/hub.css", headers={"accept-encoding": "identity"})
    zipped = client.get("/assets/hub.css", headers={"accept-encoding": "gzip, br"})
    assert "content-encoding" not in plain.headers
    assert (
        zipped.headers["content-encoding"] == "gzip" and "accept-encoding" in zipped.headers["vary"].lower()
    )
    assert zipped.content == plain.content  # the client decompresses: same CSS
    assert int(zipped.headers["content-length"]) < len(plain.content) / 2


def test_binary_assets_are_not_recompressed(client: TestClient) -> None:
    r = client.get("/assets/fonts/nunito-sans-latin-wght-normal.woff2", headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in r.headers


def test_the_compressed_catalog_is_valid_gzip() -> None:
    from hub.routes_static import i18n_bytes

    raw, zipped, _ = i18n_bytes("en")
    assert gzip.decompress(zipped) == raw
