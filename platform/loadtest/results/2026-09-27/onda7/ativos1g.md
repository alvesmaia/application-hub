# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) — 2026-09-27T20:34:42.992Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| ativos | **50** | 75 | lista p95 926 ms · detalhe p95 945 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ativos-u10 | ✅ | 3 | 93.1 | 76.3 | — | 1040.7 | 1151.4 | — | 2.0 | 0 | 0 | 8.8 | 9% | 21MiB | 1% | 139 MiB | 24% | 35 |
| ativos-u15 | ✅ | 4.3 | 158.1 | 109 | 26.9 | 1057.4 | 1108.6 | — | 1.0 | 0 | 0 | 10.4 | 3% | 21MiB | 1% | 139 MiB | 31% | 35 |
| ativos-u20 | ✅ | 5.6 | 207.7 | 197.9 | 19.9 | 1040.9 | 1089.5 | — | 1.0 | 0 | 0 | 6.8 | 4% | 24MiB | 1% | 139 MiB | 45% | 35 |
| ativos-u25 | ✅ | 7 | 241.2 | 186.9 | 110.4 | 3096.6 | 1149.9 | — | 1.0 | 0 | 0 | 10.8 | 9% | 26MiB | 1% | 139 MiB | 53% | 28 |
| ativos-u30 | ✅ | 8.9 | 277.5 | 182.9 | 69.1 | 1047.9 | 4241.7 | — | 1.0 | 0 | 0.25 | 8.4 | 10% | 32MiB | 2% | 139 MiB | 72% | 15 |
| ativos-u40 | ✅ | 11.7 | 339.4 | 281.1 | 91 | 1116.3 | 1138.5 | — | 1.0 | 0 | 0 | 8.2 | 8% | 36MiB | 3% | 139 MiB | 70% | 18 |
| ativos-u50 | ✅ | 14.2 | 468 | 326.2 | 102.2 | 1147.6 | 2057.3 | — | 1.0 | 0 | 0.31 | 12.7 | 14% | 42MiB | 4% | 139 MiB | 102% | 19 |
| ativos-u75 | ❌ | 20.9 | 926 | 945 | 170.6 | 3088.8 | 4261.2 | — | 1.5 | 0 | 1.38 | 76.7 | 24% | 52MiB | 6% | 142 MiB | 144% | 24 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=262 MB · object store: 328.0K	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-contratos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-db cpus=2000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
