# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · hub-db com 2 vCPU — 2026-09-27T13:48:22.491Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| ativos | **75** | 100 | lista p95 715.6 ms · detalhe p95 1002.1 ms |
| all | **150** | 200 | lista p95 2415.3 ms · detalhe p95 851 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ativos-u10 | ✅ | 2.9 | 70.4 | 118.2 | 20.5 | 1078.8 | 4095.8 | — | 1.0 | 0 | 0 | 6.3 | 6% | 10MiB | 1% | 320 MiB | 20% | 8 |
| ativos-u25 | ✅ | 7.3 | 120 | 200.3 | 78.7 | 1039.2 | 4583.6 | — | 1.0 | 0 | 0 | 30.8 | 16% | 28MiB | 4% | 320 MiB | 35% | 11 |
| ativos-u35 | ✅ | 10.2 | 123.9 | 158.9 | 65.7 | 2109.9 | 1245.2 | — | 1.1 | 0 | 0 | 14.5 | 16% | 44MiB | 3% | 317 MiB | 58% | 11 |
| ativos-u50 | ✅ | 15.1 | 187 | 263.9 | 38.4 | 1214.4 | 4456.6 | — | 1.0 | 0 | 0.15 | 42.4 | 19% | 49MiB | 3% | 317 MiB | 81% | 15 |
| ativos-u75 | ✅ | 21.7 | 307.9 | 376.6 | 156.1 | 2140.9 | 2080.8 | — | 1.0 | 0 | 0.2 | 37.2 | 18% | 49MiB | 4% | 318 MiB | 122% | 21 |
| ativos-u100 | ❌ | 26.9 | 715.6 | 1002.1 | 217.3 | 4192.5 | 5014.6 | — | 1.1 | 0 | 0.58 | 80 | 27% | 70MiB | 6% | 319 MiB | 150% | 22 |
| all-u10 | ✅ | 2.8 | 66.1 | 67.9 | 855.5 | 1063.3 | 1054.7 | 27.3 | — | 0 | 0 | 5.2 | 0%/0%/0% | 69MiB/3MiB/2MiB | 2% | 318 MiB | 13% | 23 |
| all-u25 | ✅ | 6.9 | 133.3 | 98.5 | 610.8 | 1062.6 | 1091.7 | 39.3 | 1.0 | 0 | 0 | 6.3 | 3%/0%/0% | 39MiB/5MiB/2MiB | 2% | 319 MiB | 30% | 25 |
| all-u35 | ✅ | 9.7 | 71.6 | 67.7 | 2764.9 | 1073.4 | 1056.4 | 42.9 | 1.0 | 0 | 0 | 5.1 | 3%/0%/0% | 37MiB/6MiB/2MiB | 2% | 318 MiB | 30% | 25 |
| all-u50 | ✅ | 13.4 | 108.5 | 127.6 | 2043.4 | 1033.4 | 1110.9 | 53.2 | 1.1 | 0 | 0.33 | 15.3 | 10%/1%/0% | 41MiB/10MiB/2MiB | 3% | 318 MiB | 46% | 25 |
| all-u75 | ✅ | 20.6 | 144.8 | 155.9 | 2076.9 | 1053.5 | 1129.4 | 68 | 1.0 | 0 | 0 | 5.5 | 11%/1%/1% | 49MiB/12MiB/2MiB | 3% | 318 MiB | 77% | 28 |
| all-u100 | ✅ | 27 | 189.9 | 199 | 1975.6 | 1064.6 | 1125.9 | 41.4 | 1.0 | 0 | 0.16 | 7 | 6%/1%/1% | 48MiB/18MiB/3MiB | 5% | 318 MiB | 104% | 31 |
| all-u150 | ✅ | 40.3 | 278.8 | 332 | 2135.6 | 1143.6 | 1228.5 | 42.7 | 1.1 | 0 | 0.94 | 10.6 | 16%/1%/1% | 54MiB/25MiB/3MiB | 7% | 318 MiB | 140% | 32 |
| all-u200 | ❌ | 48.6 | 2415.3 | 851 | 1972.8 | 3094.4 | 4093.4 | 72.7 | 1.2 | 0 | 1.6 | 53.4 | 18%/1%/1% | 69MiB/30MiB/6MiB | 8% | 318 MiB | 150% | 37 |

Banco: postgres=7519 kB, ativos=312 MB, servicos=931 MB · object store: 759M	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-db cpus=2000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
