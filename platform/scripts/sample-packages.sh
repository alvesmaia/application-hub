#!/usr/bin/env bash
# Gera os pacotes .hubapp de exemplo em platform/samples/ com o `hub pack` real,
# a partir das pastas em platform/samples/src/<app>/ (manifest.json + ui/).
#
#   válidos:   ativos-2.5.0.hubapp, fornecedores-1.9.0.hubapp, servicos-3.2.0.hubapp,
#              contratos-1.1.0.hubapp, coletor-1.0.0.hubapp (Python/FastAPI)
#   inválidos: invalido-segredo-ativos-2.5.1.hubapp        (ui/.env com token → Segurança)
#              invalido-script-inline-fornecedores-1.9.1.hubapp (<script> inline → Front-end)
#
# Imagem: cada pacote aponta para sua imagem Python própria (ex.: `hub-ativos`):
#
#   hub-ativos@sha256:<Image ID>
#
# Um build local não tem RepoDigest de registro (só existe depois de push/pull);
# por isso o digest usado é o Image ID (`docker image inspect -f '{{.Id}}'`). O
# hub-runtime resolve os dois casos: primeiro `nome@sha256:…` como RepoDigest,
# depois `sha256:…` como Image ID de uma imagem local do mesmo repositório.
# Rode de novo depois de reconstruir a imagem Python do app.
#
# Sem Docker (ou com HUB_SAMPLE_IMAGE=ghcr), usa referências determinísticas
# `ghcr.io/org/<app>@sha256:<sha256 do id>`: o pipeline valida o formato, mas o
# deploy no runtime falha (imagem inexistente) e restaura a versão anterior.
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

# Inválido 1: arquivo de segredo dentro de ui/ (o `hub pack` inclui ui/**).
cp -r "$SRC/ativos" "$TMP/segredo"
sed -i 's/"2.5.0"/"2.5.1"/' "$TMP/segredo/manifest.json"
printf 'API_TOKEN=ghp_%s\n' "$(digest token | head -c 36)" > "$TMP/segredo/ui/.env"
"$HUB" pack --dir "$TMP/segredo" --image "$(image ativos)" --out "$OUT/invalido-segredo-ativos-2.5.1.hubapp"

# Inválido 2: script inline no fragmento (incompatível com a CSP).
cp -r "$SRC/fornecedores" "$TMP/inline"
sed -i 's/"1.9.0"/"1.9.1"/' "$TMP/inline/manifest.json"
printf '<section>\n  <script>document.title = "x"</script>\n</section>\n' > "$TMP/inline/ui/index.html"
"$HUB" pack --dir "$TMP/inline" --image "$(image fornecedores)" --out "$OUT/invalido-script-inline-fornecedores-1.9.1.hubapp"

ls -l "$OUT"/*.hubapp
