#!/usr/bin/env bash
# Wheel autossuficiente da hub-cli (+ o do SDK) em platform-py/dist/cli/:
#   uv tool install platform-py/dist/cli/hub_cli-<versão>-py3-none-any.whl
# Embute: pacote `hub` (hub em processo do `hub dev`/`hub check`), assets do shell e o wheel do hub_sdk
# (copiado para vendor/ de cada projeto criado — o desenvolvedor não precisa do repositório).
set -euo pipefail
cd "$(dirname "$0")/.."
out=dist/cli
rm -rf cli/hub_cli/_wheels "$out"
mkdir -p cli/hub_cli/_wheels "$out"
uv build -q --wheel ../platform/sdk-python -o cli/hub_cli/_wheels
cp cli/hub_cli/_wheels/hub_sdk-*.whl "$out"/
uv build -q --wheel cli -o "$out"
ls -1 "$out"
