#!/usr/bin/env bash
# Prepara uma versão: atualiza a versão do hub, move o [Unreleased] do CHANGELOG para a versão, commita, cria a tag
# anotada v<versão> e um RASCUNHO de release no GitHub com as notas do CHANGELOG (nunca publica; publique na
# interface ou com `gh release edit v<versão> --draft=false`). Imagens: workflow manual .github/workflows/images.yml.
#   bash platform-py/scripts/release.sh 0.2.0 [--no-push] [--dry-run]
set -euo pipefail
cd "$(dirname "$0")/../.."
V=${1:?uso: release.sh <versão> [--no-push] [--dry-run]}
shift
PUSH=1 DRY=0
for a in "$@"; do case "$a" in --no-push) PUSH=0 ;; --dry-run) DRY=1 ;; *) echo "opção: $a" >&2; exit 2 ;; esac; done
[[ $V =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "versão inválida: $V" >&2; exit 2; }
git diff --quiet && git diff --cached --quiet || { echo "árvore com mudanças não commitadas" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && { echo "tag v$V já existe" >&2; exit 1; }

notes=$(mktemp)
awk -v v="$V" '/^## \[Unreleased\]/{f=1; next} /^## \[/{if(f) exit} f' CHANGELOG.md > "$notes"
grep -q '[^[:space:]]' "$notes" || awk -v v="$V" '$0 ~ "^## \\[" v "\\]"{f=1; next} /^## \[/{if(f) exit} f' CHANGELOG.md > "$notes"
grep -q '[^[:space:]]' "$notes" || { echo "CHANGELOG sem notas em [Unreleased] nem [$V]" >&2; exit 1; }

if [ "$DRY" = 1 ]; then
  echo "== v$V (dry-run) =="; cat "$notes"; exit 0
fi
sed -i "s/^__version__ = \"[^\"]*\"/__version__ = \"$V\"/" platform-py/hub/__init__.py
sed -i "0,/^version = \"[^\"]*\"/s//version = \"$V\"/" platform-py/pyproject.toml
if ! grep -q "^## \[$V\]" CHANGELOG.md; then
  sed -i "s/^## \[Unreleased\]$/## [Unreleased]\n\n## [$V] — $(date +%F)/" CHANGELOG.md
fi
(cd platform-py && uv lock -q && uv run python -m hub.ops.devdocs --dates)
git add platform-py/hub/__init__.py platform-py/pyproject.toml platform-py/uv.lock CHANGELOG.md platform/docs/dev/dates.json
git commit -m "chore(release): v$V"
git tag -a "v$V" -F "$notes"
if [ "$PUSH" = 1 ]; then
  git push origin HEAD "v$V"
  gh release create "v$V" --draft --title "Application Hub $V" --notes-file "$notes" --verify-tag
  echo "rascunho criado: gh release view v$V --web"
else
  echo "tag v$V criada localmente (sem push)"
fi
rm -f "$notes"
