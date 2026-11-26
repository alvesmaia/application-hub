"""`hub-check` does not test the same code twice: a green full run records the code's identity, and the next full run
(the push hook, the release's push) skips when nothing that matters changed. The hub's tests run in parallel."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import hub_check
from hub_check import already_checked, code_identity, hub_steps


def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607 - test repo
    (tmp_path / "platform-py" / "hub").mkdir(parents=True)
    (tmp_path / "platform-py" / "hub" / "__init__.py").write_text(
        '__version__ = "1.0.0"  # x\n', encoding="utf-8"
    )
    (tmp_path / "platform-py" / "pyproject.toml").write_text(
        '[project]\nversion = "1.0.0"\ndeps = ["a"]\n', encoding="utf-8"
    )
    (tmp_path / "platform-py" / "hub" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# log\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".coverage\n", encoding="utf-8")
    return tmp_path


def test_identity_ignores_docs_and_the_version_bump_but_not_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path)
    monkeypatch.setattr(hub_check, "REPO", root)
    first = code_identity()
    (root / "CHANGELOG.md").write_text("# log\n## 1.0.1\n", encoding="utf-8")
    for f, old, new in (
        ("platform-py/hub/__init__.py", "1.0.0", "1.0.1"),
        ("platform-py/pyproject.toml", "1.0.0", "1.0.1"),
    ):
        p = root / f
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    (root / ".coverage").write_bytes(b"ignored by git")
    assert code_identity() == first  # a release's bump is the same code
    (root / "platform-py" / "pyproject.toml").write_text(
        '[project]\nversion = "1.0.1"\ndeps = ["b"]\n', encoding="utf-8"
    )
    assert code_identity() != first  # a dependency is not
    (root / "platform-py" / "pyproject.toml").write_text(
        '[project]\nversion = "1.0.1"\ndeps = ["a"]\n', encoding="utf-8"
    )
    (root / "platform-py" / "hub" / "novo.py").write_text(
        "y = 2\n", encoding="utf-8"
    )  # untracked code counts too
    assert code_identity() != first


def test_skip_only_for_the_same_code_and_a_wide_enough_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_CHECK_FORCE", raising=False)  # the check itself may run forced
    assert already_checked("hub", "abc", "hub abc") and already_checked("hub", "abc", "all abc")
    assert already_checked("all", "abc", "all abc") and not already_checked("all", "abc", "hub abc")
    assert not already_checked("hub", "abc", "hub xyz") and not already_checked("hub", "abc", "")
    monkeypatch.setenv("HUB_CHECK_FORCE", "1")
    assert not already_checked("hub", "abc", "hub abc")


def test_hub_tests_run_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest_cmd = dict((n, c) for n, c, _ in hub_steps(False))["hub/pytest"]
    assert pytest_cmd[pytest_cmd.index("-n") + 1] == "4"
    monkeypatch.setenv("HUB_CHECK_WORKERS", "2")
    pytest_cmd = dict((n, c) for n, c, _ in hub_steps(False))["hub/pytest"]
    assert pytest_cmd[pytest_cmd.index("-n") + 1] == "2"
