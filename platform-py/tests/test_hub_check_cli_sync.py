"""The CLI wheel embeds a copy of the hub (`force-include`), also in the dev environment: without a reinstall the CLI
tests run against the hub of the last full sync (it was a month old). `hub-check --all` refreshes it first."""

from __future__ import annotations

import hub_check


def test_cli_steps_start_by_refreshing_the_embedded_hub() -> None:
    steps = hub_check.project_steps(hub_check.ROOT / "cli", fast=True)
    label, cmd, _ = steps[0]
    assert label == "cli/sync" and "--reinstall-package" in cmd and "hub-cli" in cmd


def test_other_projects_do_not_sync() -> None:
    sdk = hub_check.REPO / "platform" / "sdk-python"
    assert all(not label.endswith("/sync") for label, _, _ in hub_check.project_steps(sdk, fast=True))
