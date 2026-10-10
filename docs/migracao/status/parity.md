# Parity harness - status
Pronto: `platform-py/parity/compare.mjs` (+ README, ignore.json, routes.json). Ver README para uso e limiares.

Primeira execucao (2026-10-06):
- `/`: FAIL esperado. Rust 200 (Inicio) x Py 403 "Acesso negado" (admin barrado no placeholder). DOM falta/extra 257/11 (js e cru), pixels 3.83% / 11.05%.
- `/login`: FAIL minimo. 200/200, pixels 0.00% / 0.00%. DOM 3 falta / 2 extra: Python sem `<meta name=color-scheme content=light>` e ordem de metas do head diferente.

Limitacoes: viewport apenas (nao full page); tema claro apenas; so GET.
