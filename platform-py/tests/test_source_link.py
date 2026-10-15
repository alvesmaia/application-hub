"""GitHub link in the global header: HUB_SOURCE_URL (default = the project repo; empty or non-https = hidden)."""

from dataclasses import replace
from pathlib import Path

import pytest
from api_helper import api_hub
from fastapi.testclient import TestClient

from hub.config import DEFAULT_SOURCE_URL, load_settings


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return api_hub(tmp_path, monkeypatch)


def _source_link(html: str) -> str:
    header = html.split('<header class="platform-header">', 1)[1].split("</header>", 1)[0]
    if "data-hub-source" not in header:
        return ""
    return header.split("data-hub-source", 1)[1].split("</a>", 1)[0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "https://github.com/alvesmaia/application-hub"),
        ("https://git.example.com/acme/hub", "https://git.example.com/acme/hub"),
        ("", ""),
        ("   ", ""),
        ("http://github.com/x/y", ""),
        ("javascript:alert(1)", ""),
        ("https://", ""),
    ],
)
def test_source_url_setting(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str) -> None:
    monkeypatch.delenv("HUB_CONFIG_FILE", raising=False)
    if value is None:
        monkeypatch.delenv("HUB_SOURCE_URL", raising=False)
    else:
        monkeypatch.setenv("HUB_SOURCE_URL", value)
    assert load_settings().source_url == expected
    assert DEFAULT_SOURCE_URL == "https://github.com/alvesmaia/application-hub"


def test_header_shows_default_source_link(hub: TestClient) -> None:
    link = _source_link(hub.get("/").text)
    assert f'href="{DEFAULT_SOURCE_URL}"' in link
    assert 'target="_blank" rel="noopener"' in link
    assert 'aria-label="Código-fonte no GitHub (abre em nova guia)"' in link
    assert 'title="Código-fonte no GitHub"' in link
    assert 'fill="currentColor"' in link and 'aria-hidden="true"' in link
    en = _source_link(hub.get("/", headers={"accept-language": "en"}).text)
    assert 'aria-label="Source code on GitHub (opens in a new tab)"' in en


@pytest.mark.parametrize(("url", "shown"), [("https://git.example.com/acme/hub", True), ("", False)])
def test_header_source_link_follows_setting(hub: TestClient, url: str, shown: bool) -> None:
    web = hub.app.state.web
    web.settings = replace(web.settings, source_url=url)
    link = _source_link(hub.get("/").text)
    assert (f'href="{url}"' in link) is shown and bool(link) is shown
