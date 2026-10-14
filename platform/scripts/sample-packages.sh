#!/usr/bin/env bash
# Generates the sample .hubapp packages in platform/samples/ with the real `hub pack`,
# from the folders in platform/samples/src/<app>/ (manifest.json + ui/).
#
#   valid:      ativos-2.5.0.hubapp, fornecedores-1.9.0.hubapp, servicos-3.2.0.hubapp,
#              contratos-1.1.0.hubapp, coletor-1.0.0.hubapp (Python/FastAPI)
#   invalid:    invalido-segredo-ativos-2.5.1.hubapp        (ui/.env with token → "Segurança" section)
#              invalido-script-inline-fornecedores-1.9.1.hubapp (inline <script> → Front-end)
#
# Image: each package points to its own Python image (e.g. `hub-ativos`):
#
#   hub-ativos@sha256:<Image ID>
#
# A local build has no registry RepoDigest (it only exists after push/pull);
# so the digest used is the Image ID (`docker image inspect -f '{{.Id}}'`). The
# hub-runtime resolves both cases: first `name@sha256:…` as RepoDigest,
# then `sha256:…` as the Image ID of a local image from the same repository.
# Run again after rebuilding the app's Python image.
#
# Without Docker (or with HUB_SAMPLE_IMAGE=ghcr), uses deterministic references
# `ghcr.io/org/<app>@sha256:<sha256 of the id>`: the pipeline validates the format, but the
# deploy at runtime fails (image does not exist) and the previous version is restored.
set -euo pipefail
cd "$(dirname "$0")/.."

HUB=../platform-py/cli/hub
SRC=samples/src
OUT=samples
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

digest() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

image() {
  local name="hub-$1" id=""
  if [ "${HUB_SAMPLE_IMAGE:-local}" = "local" ] && command -v docker >/dev/null 2>&1; then
    id="$(docker image inspect -f '{{.Id}}' "$name" 2>/dev/null || true)"
  fi
  if [ -n "$id" ]; then echo "$name@$id"; else echo "ghcr.io/org/$1@sha256:$(digest "$1")"; fi
}

for app in ativos fornecedores servicos coletor; do
  version=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/$app/manifest.json")
  "$HUB" pack --dir "$SRC/$app" --image "$(image "$app")" --out "$OUT/$app-$version.hubapp"
done

# Invalid 1: secret file inside ui/ (`hub pack` includes ui/**).
cp -r "$SRC/ativos" "$TMP/segredo"
sed -i 's/"2.5.0"/"2.5.1"/' "$TMP/segredo/manifest.json"
printf 'API_TOKEN=ghp_%s\n' "$(digest token | head -c 36)" > "$TMP/segredo/ui/.env"
"$HUB" pack --dir "$TMP/segredo" --image "$(image ativos)" --out "$OUT/invalido-segredo-ativos-2.5.1.hubapp"

# Invalid 2: inline script in the fragment (incompatible with the CSP).
cp -r "$SRC/fornecedores" "$TMP/inline"
sed -i 's/"1.9.0"/"1.9.1"/' "$TMP/inline/manifest.json"
printf '<section>\n  <script>document.title = "x"</script>\n</section>\n' > "$TMP/inline/ui/index.html"
"$HUB" pack --dir "$TMP/inline" --image "$(image fornecedores)" --out "$OUT/invalido-script-inline-fornecedores-1.9.1.hubapp"

ls -l "$OUT"/*.hubapp
