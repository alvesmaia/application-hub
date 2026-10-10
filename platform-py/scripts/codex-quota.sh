#!/usr/bin/env bash
# Mostra o uso da cota do Codex (último registro de rate_limits nas sessões locais).
# Saída: "<used_percent> <reset_em_iso>"; código 2 se >= ${CODEX_QUOTA_MAX:-80}%.
f=$(ls -t ~/.codex/sessions/*/*/*/*.jsonl 2>/dev/null | head -1)
[ -z "$f" ] && { echo "desconhecido"; exit 1; }
line=$(grep -o '"primary":{"used_percent":[0-9.]*,"window_minutes":[0-9]*,"resets_at":[0-9]*' "$f" | tail -1)
pct=$(echo "$line" | sed -E 's/.*"used_percent":([0-9.]+).*/\1/')
reset=$(echo "$line" | sed -E 's/.*"resets_at":([0-9]+).*/\1/')
echo "$pct $(date -d @"$reset" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$reset")"
awk -v p="$pct" -v m="${CODEX_QUOTA_MAX:-80}" 'BEGIN{exit (p>=m)?2:0}'
