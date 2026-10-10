#!/usr/bin/env bash
# Gera planilhas .xlsx de ativos para testar a importação (30 colunas, ~1% de linhas
# com erro proposital: obrigatório vazio, categoria inválida, data/valor/inteiro
# inválidos ou patrimônio duplicado).
#
#   ./demo-apps/samples/gerar-planilha.sh               # 100 mil linhas (~12 MB)
#   ./demo-apps/samples/gerar-planilha.sh 20000 out.xlsx 50
#
# Arquivos grandes não são commitados (ver .gitignore); o pequeno
# `ativos-1000.xlsx` fica no repositório. Rode a partir de platform/.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROWS="${1:-100000}"
OUT="${2:-demo-apps/samples/ativos-${ROWS}.xlsx}"
BAD="${3:-10}"
PREFIX="${4:-IMP-}"
cargo run -q --release -p hub-demo-apps -- gen-xlsx "$ROWS" "$OUT" "$BAD" "$PREFIX"
