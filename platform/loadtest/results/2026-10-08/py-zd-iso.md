# Zero downtime — pilha Python (2026-10-08T22:15:53.032Z)

Carga 60 req/s.

| Fase | s | req | 2xx | não-2xx | erro |
|---|---:|---:|---:|---|---|
| Aplicar (blue/green) #1 | 12.5 | 707 | 707 | {} |  |
| Aplicar (blue/green) #2 | 10.2 | 592 | 592 | {} |  |
| Importar 2.9.97728 em produção + Aprovar (blue/green) | 13.4 | 734 | 734 | {} |  |
| Rollback (blue/green) | 10.9 | 604 | 604 | {} |  |

Total: 2843 req, 2xx 2843, não-2xx {}

- 0.1 s — login ok · carga 60 req/s em /_frag/ativos/dashboard, /_frag/ativos/assets?q=NT-1
- 5.2 s — linha de base: 279 req, 279 2xx, não-2xx {}
- 17.6 s — Aplicar (blue/green) #1: 707 req em 12.5 s · 2xx 707 · não-2xx {}
- 27.8 s — Aplicar (blue/green) #2: 592 req em 10.2 s · 2xx 592 · não-2xx {}
- 41.6 s — Importar 2.9.97728 em produção + Aprovar (blue/green): 734 req em 13.4 s · 2xx 734 · não-2xx {}
- 52.5 s — Rollback (blue/green): 604 req em 10.9 s · 2xx 604 · não-2xx {}
- 52.6 s — total: 2843 req · 2xx 2843 · não-2xx {}
