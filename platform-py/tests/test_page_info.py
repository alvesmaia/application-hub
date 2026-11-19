"""The page description lives behind an "i" next to the title, which opens a side panel; no screen keeps the
subtitle line under the title (the Home greeting count is a status line, not a description)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient
from test_english_pages import _routes

PANEL = re.compile(r'<aside id="hub-page-info"[^>]*data-hub-info=""[^>]*>(.*?)</aside>', re.S)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def test_tasks_description_is_in_the_side_panel(hub: TestClient) -> None:
    page = hub.get("/admin/tasks").text
    assert 'data-hub-info-open=""' in page and 'aria-controls="hub-page-info"' in page
    panel = PANEL.search(page)
    assert panel and "Importações, exportações e outros processos longos" in panel.group(1)
    assert 'class="page-subtitle"' not in page


def test_no_screen_keeps_the_subtitle_line(hub: TestClient) -> None:
    left = [r for r in _routes() if r != "/" and 'class="page-subtitle"' in hub.get(r).text]
    assert not left, left


def test_backups_note_moved_into_the_panel(hub: TestClient) -> None:
    page = hub.get("/admin/backups").text
    panel = PANEL.search(page)
    assert panel and "credenciais só de leitura dos backups" in panel.group(1)
    assert page.count("credenciais só de leitura dos backups") == 1


def test_the_i_keeps_the_serif_italic_glyph() -> None:
    """styles.css forces the UI font on every button with !important; the "i" buttons must win over it."""
    css = (Path(__file__).resolve().parents[2] / "platform/assets/shell/hub.css").read_text("utf-8")
    assert '.hub-info-toggle,.hub-stack-toggle{font-family:Georgia,"Times New Roman",serif!important}' in css
