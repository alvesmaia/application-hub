"""Pre-push: the full hub-check (~20 min) only when the push carries code; a push of docs, CHANGELOG/TASKS or the
release's version bump runs `hub-check --fast` (ruff, format, ty) only."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"


def _hooks() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(CONFIG.read_text("utf-8"))
    return {h["id"]: h for repo in doc["repos"] for h in repo["hooks"]}


def _runs_full(path: str) -> bool:
    hook = _hooks()["hub-check"]
    return not hook.get("always_run") and not re.search(hook["exclude"], path)


def test_light_pushes_skip_the_full_suite() -> None:
    for path in ("CHANGELOG.md", "TASKS.md", "docs/ATUALIZACAO.md", "platform/docs/CONTRATO-APP.md",
                 "platform/docs/dev/dates.json", "platform-py/hub/__init__.py", "platform-py/pyproject.toml",
                 "platform-py/uv.lock", "docs/task-details.md"):  # fmt: skip
        assert not _runs_full(path), path


def test_code_still_runs_the_full_suite() -> None:
    for path in ("platform-py/hub/gate.py", "platform-py/hub/web/templates/base.html", "platform/assets/shell/hub.css",
                 "platform-py/docker-compose.yml", "platform-py/scripts/install.sh", "platform-py/tests/test_x.py",
                 "platform/demo-apps-py/automations/src/automations/app.py", "platform/sdk-python/README.md"):  # fmt: skip
        assert _runs_full(path) or path.endswith(".md"), path


def test_the_fast_check_always_runs_on_push() -> None:
    fast = _hooks()["hub-check-fast"]
    assert (
        fast["always_run"] and fast["stages"] == ["pre-push"] and fast["entry"].endswith("hub-check --fast")
    )
