"""Every sample app is wired everywhere a deployment needs it: compose (dev, prod, published images), its own
network and broker range, its database, secrets in the production template, the registry and the CI image build.
A sample app added in one place and forgotten in another only fails at install time on a real server."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hub.admin.demo_db import DEMO_APPS

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


@pytest.mark.parametrize("app", DEMO_APPS)
def test_sample_app_is_wired_in_every_deployment_file(app: str) -> None:
    up = app.upper()
    assert f"  app-hub-app-{app}:" in _read("docker-compose.apps.yml")
    assert f"  app-hub-app-{app}:" in _read("docker-compose.prod.yml")
    assert f"app-hub-{app}:${{HUB_VERSION:?}}" in _read("docker-compose.ghcr.yml")
    base = _read("docker-compose.yml")
    assert (
        f"  hub-app-{app}:" in base and f"      - hub-app-{app}\n" in base and f"{app}=${{HUB_SUBNET" in base
    )
    example = _read("prod.env.example")
    assert f"APP_HUB_APP_DB_PASSWORD_{up}=<openssl rand -hex 24>" in example
    assert f"HUB_INTERNAL_TOKEN_{up}=<openssl rand -hex 24>" in example
    assert app in _read("infra/app-db-init.sh")
    for script in ("scripts/conformance.sh", "scripts/iso-instance.sh"):
        assert re.search(rf"\(([a-z]+ )*{app}[ )]", _read(script)), script
    assert app in _read("../.github/workflows/images.yml")
    for seeds in ("app-env.json", "app-env.prod.json"):  # the internal token the hub and the app share
        names = {v["name"] for v in json.loads(_read(seeds))}
        assert f"HUB_INTERNAL_TOKEN_{up}" in names, seeds


@pytest.mark.parametrize("app", DEMO_APPS)
def test_sample_app_is_in_the_registry_with_its_manifest(app: str) -> None:
    entries = {e["manifest"]["id"]: e for e in json.loads(_read("apps.json"))}
    assert entries[app]["upstream"] == f"http://app-hub-app-{app}:8080"
    manifest = ROOT.parent / "platform" / "demo-apps-py" / app / "manifest.json"
    if manifest.exists():  # coletor builds from platform/Dockerfile.coletor and keeps its manifest elsewhere
        assert entries[app]["manifest"]["id"] == json.loads(manifest.read_text("utf-8"))["id"]


def test_broker_ranges_do_not_overlap() -> None:
    line = next(x for x in _read("docker-compose.yml").splitlines() if "HUB_BROKER_APP_CIDRS" in x)
    ranges = [part.split("=", 1)[1] for part in line.split('"')[1].split(",")]
    assert len(ranges) == len(set(ranges)) == len(DEMO_APPS)


SAMPLE_CPU = (
    0.5  # sample apps are for evaluation: half a CPU each (the hub's default for apps without resources)
)


@pytest.mark.parametrize("app", DEMO_APPS)
def test_sample_apps_ask_for_half_a_cpu(app: str) -> None:
    entry = next(e for e in json.loads(_read("apps.json")) if e["manifest"]["id"] == app)
    assert float((entry["manifest"].get("resources") or {}).get("cpu", SAMPLE_CPU)) <= SAMPLE_CPU
    manifest = ROOT.parent / "platform" / "demo-apps-py" / app / "manifest.json"
    if manifest.exists():
        res = json.loads(manifest.read_text("utf-8")).get("resources") or {}
        assert float(res.get("cpu", SAMPLE_CPU)) <= SAMPLE_CPU
    block = _read("docker-compose.apps.yml").split(f"  app-hub-app-{app}:\n", 1)[1].split("\n\n", 1)[0]
    assert f"cpus: {SAMPLE_CPU}" in block, app
