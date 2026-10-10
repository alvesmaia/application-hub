# Carga realista — APP_MEM=512m, 1 vCPU por app (VM de destino) — 2026-09-27T13:32:13.437Z

Gerado por `loadtest/realistic.mjs run --mem 512m`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| ativos | **25** | 35 | lista p95 539.8 ms · detalhe p95 596 ms |
| fornecedores | **800** | 1200 | detalhe p95 5037 ms · erros 1.39% |
| servicos | **200** | 300 | lista p95 760.9 ms |
| all | **50** | 75 | detalhe p95 656.2 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ativos-u10 | ✅ | 3 | 246.9 | 340.1 | 25.2 | 1069.9 | 1182.3 | — | 1.0 | 0 | 0 | 4.4 | 3% | 25MiB | 1% | 97 MiB | 22% | 9 |
| ativos-u25 | ✅ | 7.7 | 240.3 | 452.7 | 94.3 | 1104.6 | 3437.8 | — | 1.0 | 0 | 0.57 | 10.5 | 9% | 33MiB | 3% | 98 MiB | 40% | 13 |
| ativos-u35 | ❌ | 9.8 | 539.8 | 596 | 96.9 | 1146.2 | 6695.1 | — | 1.2 | 0 | 0 | 115.1 | 18% | 43MiB | 4% | 98 MiB | 59% | 21 |
| fornecedores-u10 | ✅ | 2.8 | 3.7 | 7.3 | 4003.5 | 4.6 | 6.9 | 32 | — | 0 | 0 | 4.5 | 0% | 3MiB | 1% | 98 MiB | 1% | 21 |
| fornecedores-u25 | ✅ | 7 | 4.4 | 6.2 | 2582.9 | 5.5 | 4.4 | 30.3 | — | 0 | 0 | 5.7 | 0% | 5MiB | 1% | 98 MiB | 2% | 21 |
| fornecedores-u35 | ✅ | 9.7 | 4.3 | 6.3 | 2066.5 | 4.5 | 7 | 27.1 | — | 0 | 0 | 4.7 | 1% | 4MiB | 1% | 97 MiB | 1% | 21 |
| fornecedores-u50 | ✅ | 13.9 | 4.2 | 5.6 | 2090.5 | 6.2 | 4.9 | 47.7 | — | 0 | 0 | 6.6 | 1% | 5MiB | 2% | 98 MiB | 1% | 21 |
| fornecedores-u75 | ✅ | 20.9 | 3.8 | 5.9 | 2081.9 | 6.3 | 10.7 | 28.6 | — | 0 | 0 | 4.5 | 1% | 6MiB | 3% | 98 MiB | 1% | 21 |
| fornecedores-u100 | ✅ | 28.1 | 4.1 | 6.7 | 1989.6 | 7 | 6.6 | 33.4 | — | 0 | 0 | 4.1 | 1% | 8MiB | 3% | 98 MiB | 1% | 21 |
| fornecedores-u150 | ✅ | 42.7 | 4.2 | 46.4 | 2131.9 | 6.6 | 5.5 | 31.5 | — | 0 | 0 | 8.7 | 2% | 10MiB | 4% | 98 MiB | 1% | 21 |
| fornecedores-u200 | ✅ | 57 | 3.6 | 46.6 | 2061.8 | 6 | 8.5 | 56.9 | — | 0 | 0 | 9 | 3% | 16MiB | 5% | 98 MiB | 1% | 21 |
| fornecedores-u300 | ✅ | 85.2 | 3.5 | 10.3 | 2061.5 | 6 | 7.4 | 61.6 | — | 0 | 0 | 4.5 | 4% | 27MiB | 7% | 104 MiB | 1% | 21 |
| fornecedores-u400 | ✅ | 113 | 4.5 | 11.1 | 2052.3 | 9 | 8.2 | 43.7 | — | 0 | 0 | 8.7 | 5% | 43MiB | 10% | 106 MiB | 1% | 21 |
| fornecedores-u600 | ✅ | 170 | 7.2 | 38.6 | 2061.8 | 17.7 | 20.4 | 56.3 | — | 0 | 0.1 | 10.6 | 8% | 87MiB | 14% | 130 MiB | 1% | 21 |
| fornecedores-u800 | ✅ | 225.4 | 9.3 | 149.4 | 2105.6 | 93 | 92.8 | 162.4 | — | 0 | 2.07 | 18.1 | 10% | 163MiB | 17% | 145 MiB | 1% | 21 |
| fornecedores-u1200 | ❌ | 297.8 | 14.1 | 5037 | 2098.4 | 19.3 | 17.8 | 4653.8 | — | 1.39 | 10.97 | 15.9 | 14% | 315MiB | 23% | 296 MiB | 1% | 21 |
| servicos-u10 | ✅ | 2.5 | 51.8 | 7.2 | 17.1 | 433.2 | 389.2 | — | — | 0 | 0 | 4.7 | 0% | 2MiB | 1% | 295 MiB | 12% | 24 |
| servicos-u25 | ✅ | 6.2 | 44.1 | 14.5 | 16 | 520.9 | 829.4 | — | — | 0 | 0.72 | 4.9 | 1% | 2MiB | 1% | 296 MiB | 23% | 25 |
| servicos-u35 | ✅ | 8.8 | 22 | 14.5 | 17.8 | 535.3 | 704.9 | — | — | 0 | 0.5 | 4.9 | 1% | 2MiB | 2% | 295 MiB | 25% | 26 |
| servicos-u50 | ✅ | 12.3 | 30.7 | 14.8 | 34.6 | 606.4 | 605.4 | — | — | 0 | 1.98 | 4.7 | 1% | 3MiB | 2% | 295 MiB | 41% | 25 |
| servicos-u75 | ✅ | 18.6 | 68 | 47.3 | 102.4 | 808.7 | 1207.2 | — | — | 0 | 3.83 | 4.1 | 1% | 3MiB | 2% | 295 MiB | 61% | 29 |
| servicos-u100 | ✅ | 25 | 70.5 | 58.9 | 79 | 1003.3 | 795.5 | — | — | 0 | 4.26 | 4.8 | 1% | 4MiB | 2% | 297 MiB | 66% | 33 |
| servicos-u150 | ✅ | 38 | 75 | 61.8 | 108 | 1021 | 879.6 | — | — | 0 | 6.84 | 3.6 | 2% | 4MiB | 3% | 295 MiB | 75% | 34 |
| servicos-u200 | ✅ | 49.8 | 143.3 | 78.8 | 114.2 | 1489.9 | 1198.7 | — | — | 0 | 9.23 | 9.6 | 3% | 4MiB | 4% | 295 MiB | 82% | 34 |
| servicos-u300 | ❌ | 74.3 | 760.9 | 394.9 | 184.7 | 1311.7 | 1486.6 | — | — | 0 | 10.38 | 11.9 | 3% | 7MiB | 5% | 296 MiB | 86% | 34 |
| all-u10 | ✅ | 2.9 | 111.4 | 138.1 | 901.1 | 423.4 | 1061.3 | 39.8 | 1.0 | 0 | 0 | 4.4 | 1%/0%/0% | 30MiB/314MiB/7MiB | 1% | 296 MiB | 11% | 34 |
| all-u25 | ✅ | 6.9 | 212.9 | 160 | 3817.1 | 1046.8 | 5015.5 | 36.8 | 1.0 | 0 | 0 | 9.4 | 6%/0%/0% | 39MiB/314MiB/7MiB | 3% | 295 MiB | 22% | 34 |
| all-u35 | ✅ | 9.9 | 123.8 | 190.7 | 2045.9 | 1089.7 | 1094.5 | 39.1 | 1.1 | 0 | 0 | 6 | 3%/1%/0% | 32MiB/314MiB/7MiB | 2% | 295 MiB | 32% | 34 |
| all-u50 | ✅ | 13.6 | 322 | 298.2 | 724.2 | 1024.7 | 1340 | 41.4 | 1.1 | 0 | 0.16 | 4.7 | 4%/1%/1% | 37MiB/314MiB/7MiB | 3% | 295 MiB | 49% | 34 |
| all-u75 | ❌ | 20.4 | 407.2 | 656.2 | 2043.5 | 1553.3 | 3234.3 | 42.6 | 1.0 | 0 | 0.33 | 7.1 | 12%/1%/0% | 48MiB/315MiB/7MiB | 4% | 296 MiB | 55% | 34 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=930 MB · object store: 722M	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=536870912 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=536870912 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=536870912 cpuset=0-3
hub-db cpus=1000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
