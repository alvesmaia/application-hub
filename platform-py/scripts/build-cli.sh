#!/usr/bin/env bash
# Self-contained hub-cli wheel (+ the SDK one) in platform-py/dist/cli/:
#   uv tool install platform-py/dist/cli/hub_cli-<version>-py3-none-any.whl
# Bundles: the `hub` package (in-process hub for `hub dev`/`hub check`), shell assets and the hub_sdk wheel
# (copied to vendor/ of each created project — the developer does not need the repository).
set -euo pipefail
cd "$(dirname "$0")/.."
out=dist/cli
rm -rf cli/hub_cli/_wheels "$out"
mkdir -p cli/hub_cli/_wheels "$out"
uv build -q --wheel ../platform/sdk-python -o cli/hub_cli/_wheels
cp cli/hub_cli/_wheels/hub_sdk-*.whl "$out"/
uv build -q --wheel cli -o "$out"
ls -1 "$out"
