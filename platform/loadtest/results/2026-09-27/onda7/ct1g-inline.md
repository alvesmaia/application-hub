# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=inline — 2026-09-27T20:42:03.303Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| contratos | **30** | 40 | lista p95 655.9 ms · detalhe p95 606.3 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| contratos-u10 | ✅ | 3.1 | 264.4 | 138.9 | 144.4 | 1113.8 | 3126.1 | — | 1.0 | 0 | 0 | 4.7 | 9% | 57MiB | 1% | 163 MiB | 14% | 30 |
| contratos-u15 | ✅ | 4.7 | 266.8 | 24.6 | 71.8 | 1160.9 | 1081.9 | — | — | 0 | 0 | 5.8 | 14% | 71MiB | 1% | 163 MiB | 21% | 30 |
| contratos-u20 | ✅ | 6.2 | 417.8 | 318.1 | 1002.5 | 1270.2 | 11301.4 | — | 1.1 | 0 | 0.36 | 5.6 | 46% | 76MiB | 2% | 163 MiB | 24% | 36 |
| contratos-u25 | ✅ | 7.5 | 258.4 | 181.6 | 173.5 | 1464.9 | 4575 | — | 1.1 | 0 | 1.18 | 5.1 | 26% | 88MiB | 2% | 163 MiB | 30% | 36 |
| contratos-u30 | ✅ | 9 | 304.4 | 226.8 | 423.5 | 2263.8 | 4557.4 | — | 1.2 | 0 | 0.25 | 6.8 | 37% | 91MiB | 2% | 163 MiB | 32% | 36 |
| contratos-u40 | ❌ | 11.2 | 655.9 | 606.3 | 1060.2 | 10316 | 13514.2 | — | 10.5 | 0 | 1.39 | 6.5 | 49% | 108MiB | 2% | 163 MiB | 41% | 40 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=291 MB · object store: 184.0K	/data

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
