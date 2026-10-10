# Testes de carga — perfil VM de destino

Simula a máquina de produção alvo (VM de destino: 4 vCPU compartilhadas, 8 GB RAM) no Docker
local e mede apps e hub **através do gateway** (`/_frag/<id>/...`), como o tráfego real.
Resultados e recomendações: [`RESULTADOS.md`](RESULTADOS.md).

## Peças

| Arquivo | O que faz |
| --- | --- |
| `../docker-compose.vm-destino.yml` | Override: todos os containers da plataforma em `cpuset: "0-3"`; apps com `cpus: ${APP_CPUS:-1.0}`, `mem_limit`/`memswap_limit: ${APP_MEM:-1g}` e `HUB_DEMO_LOAD=1` (liga `/{id}/_load`); `hub-db` com `cpus: ${DB_CPUS:-2.0}` (gargalo da carga realista com 1 vCPU). Hub: 1 vCPU/1 GiB do compose base. Orçamento no cabeçalho do arquivo: 5,56 GiB de limites (≤ 8 GiB) e 6,75 vCPU de tetos em 4 vCPU (sobrecompromisso ~1,7× proposital). |
| `run.mjs` | Orquestrador (Node ≥ 18, sem dependências): sobe o compose com o override, roda o `oha` em containers `hub-load-*`, amostra `docker stats` a cada ~1 s em CSV, conta `oom`/`die` (`docker events`) e `RestartCount` por passo e grava resumos. |
| `run-all.sh` | Matriz completa: `matrix` para 512m/1g × cpus 1.0/2.0 e `noisy` para 1g × cpus 1.0/2.0; termina com `down`. |
| `results/<data>/` | `summary-*.json`/`.csv` (um registro por passo), `stats-*.csv` (amostras do `docker stats`), `env-*.txt` (HostConfig efetivo). |

## Rota de carga sintética

`GET /{id}/_load?cpu_ms=20&mem_mb=8&hold_ms=50` (em `hub-demo-apps`, só com `HUB_DEMO_LOAD=1`):
queima ~`cpu_ms` de CPU (trabalho fixo calibrado na 1ª chamada — com limite de CPU vira mais
latência, não menos trabalho), aloca e toca `mem_mb` MiB, segura por `hold_ms` e libera. Tetos no
servidor: `cpu_ms ≤ 500`, `mem_mb ≤ 256`, `hold_ms ≤ 2000`; pool de bloqueio de 32 threads.

## Como rodar

A partir de `platform/`:

```bash
docker compose -f docker-compose.yml -f docker-compose.vm-destino.yml build
docker pull ghcr.io/hatoo/oha:latest

bash loadtest/run-all.sh 60                      # tudo (~1h30), 60 s por passo
node loadtest/run.mjs matrix --mem 512m --cpus 1.0 --duration 60   # uma célula
node loadtest/run.mjs noisy  --mem 1g   --cpus 2.0 --duration 60   # hub sob vizinho barulhento
node loadtest/run.mjs down                        # compose down + remove hub-load-*
```

Opções: `--warmup 10` (s, descartado), `--out DIR`, `LOAD_CPUS=8-15` (CPUs do gerador).

### Gerador de carga

Cada passo é um (ou mais) container efêmero:

```bash
docker run --rm --name hub-load-... --cpuset-cpus 8-15 --network hub-edge \
  ghcr.io/hatoo/oha:latest -z 60s -w -c <N> -t 10s --no-tui --output-format json \
  "http://hub-web:8080/_frag/ativos/_load?cpu_ms=100"
```

Na rede `hub-edge` (10.231.0.0/24, permitida em `HUB_ADMIN_ALLOWED_CIDRS`), então as sondas de
`/admin` e `/api/admin/env` também funcionam. Sondas do cenário D: `-q 5 --latency-correction`
(5 RPS em malha aberta, sem omissão coordenada).

### Cenários

- **A** (leve): `--urls-from-file` alternando `/_frag/ativos/dashboard` e `_load?cpu_ms=5&mem_mb=2`,
  concorrência 10/50/100/200/400. Sustentável = p99 < 300 ms e erros < 1%.
- **B** (memória): `_load?cpu_ms=10&mem_mb=32&hold_ms=200`, concorrência 4/8/16/32/64 (para ao
  passar de 50% de erro); OOM via `docker events` (`oom`) e `RestartCount`.
- **C** (CPU): `_load?cpu_ms=100`, concorrência 4/16/64.
- **D** (vizinho barulhento, `noisy`): linha de base; `ativos` com C (c=64) e com B (c=64); os três
  apps com C e com B ao mesmo tempo; linha de base depois. Em todos, sondas de 5 RPS em
  `/applications`, `/admin`, `/api/admin/env` e `/_frag/servicos/dashboard`.

## Teste de caos (`chaos/chaos.mjs`)

Com o compose no ar e o seed concluído (`docker compose up -d`), a partir de `platform/`:

```bash
node loadtest/chaos/chaos.mjs --rows 250000 --burst 300 --kills 2   # ~2 min
```

Gera a planilha com a própria imagem `hub-demo-apps` (`gen-xlsx`) e calcula as linhas válidas sem
banco (`count-xlsx`); importa, dispara a rajada de cadastros com `Idempotency-Key` e novas
tentativas, derruba `hub-app-ativos` (`docker kill`) duas vezes e confere no `hub-db` e na API do hub
(`/api/admin/outbox/ativos`, `/api/admin/audit`) que a importação terminou pelo checkpoint sem
duplicados nem lote parcial, que cada gravação valeu uma vez e que cada evento de auditoria chegou
ao hub uma vez. Escreve `results/<data>/chaos.md`; sai com código 1 se alguma verificação falhar.

## Ressalvas

Docker Desktop (WSL2) num Windows com 16 CPUs lógicas: `cpuset 0-3` prende a 4 CPUs lógicas
(hyperthreads) de um host desktop, não a 4 vCPUs compartilhadas de um hypervisor; o gerador
roda na mesma VM (CPUs 8-15) e outros containers do usuário continuam ligados. vCPUs
compartilhadas da VM de destino têm mais ruído (steal time). Os números são **indicativos** — validar
na máquina real antes de fechar capacidade.

## Onda 6 — suíte de validação

Scripts reutilizáveis (compose no ar, a partir de `platform/`); resultados em
`results/<data>/onda6/` e resumo em [`RESULTADOS.md`](RESULTADOS.md#onda-6--suíte-de-validação-2026-09-27).

| Script | O que faz | Saída |
| --- | --- | --- |
| `bash loadtest/suite.sh [dir]` | roda em sequência E2E Playwright, troca sem indisponibilidade, DR, paridade, OWASP ZAP e `cargo audit`/`cargo deny` | `suite.md` (OK/FALHOU por passo; código de saída = nº de falhas) |
| `node loadtest/zero-downtime.mjs --rps 200 [--deploys 3] [--out f.md]` | 3 implantações de produção do `ativos` com imagens diferentes + rollback + staging sob carga (`oha` + sonda + POSTs idempotentes + exportação longa) | `.md`/`.json`; sai com 1 se houver não-2xx, virada suja, repetição idempotente errada, staging vazando ou exportação cortada |
| `node loadtest/dr.mjs [--out f.md]` | backup agendado → destrói banco, bucket e arquivos → restaura → confere byte a byte (RTO) | `dr.md`; sai com 1 se alguma verificação falhar |
| `node loadtest/realistic.mjs run --mem 1g [--fresh] [--apps …] [--steps …] [--db-cpus 2]` | carga realista por app com rampa até o SLO (abaixo) | `realistic-mem<m>[-db<n>cpu].{md,json}`, `stats-*.csv` |
| `node loadtest/realistic.mjs down` | remove os geradores `hub-rt-*` e derruba o compose | |
| `bash scripts/ci-publish.sh [app] [versão]` | caminho CI → hub: token de API, imagem, `hub pack`, `POST /api/ops/packages` com Bearer, aprovação, versão no ar | `ci-publish.md` |
| `node tests-e2e/parity/parity.mjs` | paridade `hub dev` × produção | `parity.md` |

### Carga realista (`realistic.mjs`)

Usuários virtuais com pausa de 2–6 s entre as ações e a mistura 60% lista/busca · 20%
detalhe/edição (POST com `Idempotency-Key`; 409/422 são respostas de negócio) · 8% exportação
CSV/XLSX · 5% relatório pesado · 5% anexo (upload + download) · 2% importação de 5 mil linhas (no
máximo 2 simultâneas). Cada app mapeia as classes nas próprias rotas (`ativos`: lista paginada com
busca/filtros/ordenação, editor + salvar, CSV filtrado, XLSX por tarefa, importação; `servicos`:
lista de chamados, busca por protocolo + mudança de status, relatório gerencial e o CSV dele;
`fornecedores`: diretório, ficha + consulta externa, PDF da ficha, anexos). `all` = os três apps
ao mesmo tempo (VUs divididos). SLO por passo: **p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício** dos apps; 429 do semáforo de operações pesadas é contado à parte (proteção
por projeto, não erro). Passos de 45 s medidos (+10 s de aquecimento), rampa padrão
10 → 1200 usuários, parando no primeiro passo fora do SLO. Também: sonda de 5 RPS em
`/applications` (latência do hub, p99), `docker stats` de hub-web/apps/hub-db/hub-objstore,
conexões ativas no Postgres e tamanho dos bancos/bucket no fim. O gerador roda em containers
`hub-rt-*` (`node:20-alpine`) na rede `hub-edge`, nas CPUs `LOAD_CPUS` (8-15), com as sessões do
admin e do operador. `--fresh` recria o compose (VM de destino) do zero com seed completo.
