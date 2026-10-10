# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) — 2026-09-27T12:56:40.378Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| ativos | **25** | 35 | lista p95 508.8 ms · detalhe p95 712 ms |
| fornecedores | **800** | 1200 | detalhe p95 4943 ms · erros 1.46% |
| servicos | **200** | 300 | lista p95 1187.9 ms · detalhe p95 636.7 ms |
| all | **50** | 75 | detalhe p95 659.6 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ativos-u10 | ✅ | 2.8 | 210.9 | 219 | 87.4 | 1041 | 5253.2 | — | 1.3 | 0 | 0 | 11.4 | 8% | 23MiB | 2% | 99 MiB | 21% | 10 |
| ativos-u25 | ✅ | 7.7 | 283.6 | 362.9 | 105 | 1103.7 | 4245.7 | — | 1.0 | 0 | 0.29 | 40.3 | 11% | 40MiB | 3% | 98 MiB | 41% | 13 |
| ativos-u35 | ❌ | 10.1 | 508.8 | 712 | 207.9 | 1301.7 | 2086.3 | — | 1.1 | 0 | 0.22 | 13.2 | 10% | 41MiB | 2% | 98 MiB | 52% | 17 |
| fornecedores-u10 | ✅ | 2.9 | 3.3 | 7.3 | 1975.2 | 4 | 3.6 | 41.1 | — | 0 | 0 | 6 | 0% | 3MiB | 1% | 98 MiB | 2% | 17 |
| fornecedores-u25 | ✅ | 7 | 3.5 | 4.8 | 2767.2 | 6.3 | 9 | 31.2 | — | 0 | 0 | 3.2 | 0% | 4MiB | 1% | 98 MiB | 1% | 17 |
| fornecedores-u35 | ✅ | 9.8 | 3 | 4.6 | 2076.7 | 5.2 | 5.4 | 22.6 | — | 0 | 0 | 4.6 | 1% | 4MiB | 1% | 98 MiB | 2% | 17 |
| fornecedores-u50 | ✅ | 13.8 | 3.4 | 5 | 2639.8 | 4.7 | 4.1 | 21.7 | — | 0 | 0 | 4.1 | 1% | 5MiB | 2% | 98 MiB | 1% | 17 |
| fornecedores-u75 | ✅ | 20.9 | 3.3 | 5.1 | 1940.2 | 5.2 | 4.7 | 24.6 | — | 0 | 0 | 3.8 | 1% | 6MiB | 3% | 98 MiB | 1% | 17 |
| fornecedores-u100 | ✅ | 28.1 | 3.3 | 6.2 | 2058.5 | 7.5 | 4.9 | 25.3 | — | 0 | 0 | 3.5 | 1% | 7MiB | 3% | 98 MiB | 1% | 17 |
| fornecedores-u150 | ✅ | 43 | 3.2 | 47.1 | 2084 | 7.4 | 5.1 | 31.5 | — | 0 | 0 | 8.1 | 2% | 10MiB | 4% | 98 MiB | 2% | 17 |
| fornecedores-u200 | ✅ | 56.4 | 3.8 | 44.1 | 2656.2 | 6.5 | 5.5 | 56.7 | — | 0 | 0 | 5.6 | 3% | 16MiB | 5% | 103 MiB | 1% | 17 |
| fornecedores-u300 | ✅ | 84.7 | 3.9 | 9.6 | 2035.1 | 8.1 | 7.7 | 65 | — | 0 | 0 | 6.2 | 4% | 26MiB | 7% | 104 MiB | 2% | 17 |
| fornecedores-u400 | ✅ | 113.2 | 4.1 | 12.9 | 2081.4 | 9.3 | 7.8 | 57.7 | — | 0 | 0 | 5.3 | 5% | 44MiB | 10% | 107 MiB | 2% | 17 |
| fornecedores-u600 | ✅ | 170.1 | 6.4 | 34 | 2042.9 | 15.2 | 18.9 | 65.3 | — | 0 | 0.2 | 13.1 | 7% | 86MiB | 12% | 108 MiB | 2% | 17 |
| fornecedores-u800 | ✅ | 225.2 | 9.6 | 254 | 2065 | 104.1 | 104.3 | 295.3 | — | 0 | 2.27 | 26.8 | 11% | 164MiB | 19% | 139 MiB | 2% | 17 |
| fornecedores-u1200 | ❌ | 300.5 | 14.2 | 4943 | 2068.6 | 18.9 | 21.8 | 4822.5 | — | 1.46 | 10.82 | 26.4 | 13% | 311MiB | 21% | 234 MiB | 2% | 17 |
| servicos-u10 | ✅ | 2.4 | 9.4 | 13.9 | 31.6 | 308.8 | 612 | — | — | 0 | 0 | 6.5 | 0% | 2MiB | 1% | 234 MiB | 7% | 19 |
| servicos-u25 | ✅ | 6.3 | 15 | 23 | 18.7 | 614.9 | 633.9 | — | — | 0 | 1.06 | 4.8 | 1% | 2MiB | 1% | 234 MiB | 17% | 21 |
| servicos-u35 | ✅ | 9.1 | 26.9 | 15.1 | 32.2 | 598.5 | 641.4 | — | — | 0 | 0.49 | 5.7 | 1% | 2MiB | 2% | 234 MiB | 32% | 21 |
| servicos-u50 | ✅ | 12.8 | 47.4 | 30.5 | 18.4 | 619.4 | 743.6 | — | — | 0 | 1.56 | 4.9 | 1% | 3MiB | 2% | 233 MiB | 40% | 22 |
| servicos-u75 | ✅ | 18.9 | 53.7 | 45.5 | 62 | 782.3 | 854.5 | — | — | 0 | 3.06 | 6 | 1% | 3MiB | 2% | 233 MiB | 50% | 24 |
| servicos-u100 | ✅ | 24.9 | 70.5 | 52.7 | 87.9 | 1067.8 | 994.6 | — | — | 0 | 5.61 | 7.3 | 1% | 3MiB | 2% | 233 MiB | 64% | 27 |
| servicos-u150 | ✅ | 37.6 | 67.2 | 59.1 | 100.8 | 900.2 | 976.5 | — | — | 0 | 7.62 | 6.1 | 2% | 3MiB | 3% | 233 MiB | 72% | 29 |
| servicos-u200 | ✅ | 50.5 | 84.5 | 77.2 | 180.3 | 1262.4 | 1184.3 | — | — | 0 | 9.15 | 3.8 | 2% | 4MiB | 4% | 233 MiB | 83% | 30 |
| servicos-u300 | ❌ | 73.5 | 1187.9 | 636.7 | 1013.8 | 1271.4 | 1234.4 | — | — | 0 | 10.79 | 10.1 | 3% | 9MiB | 5% | 234 MiB | 86% | 31 |
| all-u10 | ✅ | 2.8 | 142.9 | 119.3 | 379.9 | 1034.1 | 1107.3 | 37.5 | 1.0 | 0 | 0 | 6.9 | 2%/0%/0% | 19MiB/310MiB/8MiB | 2% | 234 MiB | 12% | 31 |
| all-u25 | ✅ | 6.8 | 155.7 | 282.4 | 2055.9 | 1089.2 | 1098.8 | 54.9 | 1.0 | 0 | 0 | 5.8 | 2%/0%/0% | 19MiB/310MiB/9MiB | 1% | 234 MiB | 26% | 31 |
| all-u35 | ✅ | 9.5 | 312.7 | 180.3 | 2516.1 | 1077.6 | 1136.2 | 32.5 | 1.0 | 0 | 0 | 5.8 | 3%/0%/0% | 30MiB/310MiB/8MiB | 3% | 233 MiB | 31% | 32 |
| all-u50 | ✅ | 13.4 | 358.4 | 461.7 | 2004.4 | 1246.9 | 1250.3 | 37.4 | 1.2 | 0 | 0.5 | 8.9 | 9%/1%/0% | 31MiB/310MiB/10MiB | 3% | 235 MiB | 49% | 31 |
| all-u75 | ❌ | 19.8 | 464.9 | 659.6 | 2765.3 | 1932.9 | 1406.5 | 48.8 | 1.2 | 0 | 1.01 | 17.3 | 4%/1%/1% | 33MiB/310MiB/8MiB | 3% | 233 MiB | 64% | 31 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=930 MB · object store: 695M	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-db cpus=1000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
