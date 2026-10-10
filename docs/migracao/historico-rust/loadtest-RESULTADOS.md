# Resultados dos testes de carga — perfil VM de destino

Execução de 2026-09-26 (dados brutos: `results/2026-09-26/`). Como rodar: [`README.md`](README.md).

## Ambiente e ressalvas

- **Host**: Windows 11, 16 CPUs lógicas, Docker Desktop 28.5 (VM WSL2, kernel 6.6, ~23 GiB para a VM).
- **Simulação da VM de destino** (`docker-compose.vm-destino.yml`): hub-web + 3 apps presos a `cpuset 0-3`;
  hub-web `cpus 1.0 / 1 GiB / cpu_shares 2048`; apps `cpu_shares 1024`, `cpus` 1.0 ou 2.0 e
  `mem_limit = memswap_limit` 512 MiB ou 1 GiB (todos os 3 apps com o mesmo valor; soma máx. 4 GiB).
- **Gerador**: `oha` 1.16 em containers `hub-load-*` com `--cpuset-cpus 8-15` na rede `hub-edge`;
  todo tráfego passa pelo gateway do hub (`http://hub-web:8080/_frag/<id>/...`). Passos de 60 s após
  aquecimento de 10 s; timeout por requisição 10 s; `docker stats` a cada ~1 s.
- **Ressalvas**: `cpuset 0-3` = 4 hyperthreads de uma CPU desktop, não 4 vCPUs compartilhadas de um
  hypervisor (na VM de destino há *steal time* e mais ruído); o gerador roda na mesma VM; outros containers
  do usuário continuavam ligados. A carga é sintética (`/_load`: CPU calibrada + memória tocada), o
  app de demo é Rust (footprint ocioso ~5–40 MiB — apps reais em JVM/Node/Python partem de 150–500
  MiB). **Números indicativos**: validar na máquina real.

## A — leve e realista (`dashboard` + `_load?cpu_ms=5&mem_mb=2` alternados)

Critério "sustentável": p99 < 300 ms e erros < 1%. Latências em ms.

| mem | cpus | c | RPS | p50 | p95 | p99 | erros | CPU app % | mem pico app | OOM/restarts |
|---|---|---|---|---|---|---|---|---|---|---|
| 512m | 1.0 | 10 | 355 | 10 | 87 | **93** | 0 | 99 | 68 MiB | 0 |
| 512m | 1.0 | 50 | 363 | 113 | 285 | 390 | 0 | 97 | 155 MiB | 0 |
| 512m | 1.0 | 200 | 358 | 512 | 1306 | 1606 | 0 | 97 | 271 MiB | 0 |
| 512m | 1.0 | 400 | 339 | 1068 | 2404 | 3099 | **5,5%** | 89 | 492 MiB | **3 OOM** |
| 1g | 1.0 | 10 | 352 | 10 | 87 | **94** | 0 | 99 | 62 MiB | 0 |
| 1g | 1.0 | 50 | 352 | 114 | 293 | 398 | 0 | 97 | 147 MiB | 0 |
| 1g | 1.0 | 400 | 351 | 911 | 2406 | 2593 | 0 | 97 | 409 MiB | 0 |
| 512m | 2.0 | 50 | 751 | 68 | 174 | **252** | 0 | 194 | 145 MiB | 0 |
| 512m | 2.0 | 100 | 740 | 123 | 363 | 488 | 0 | 193 | 189 MiB | 0 |
| 512m | 2.0 | 400 | 741 | 365 | 1173 | 1297 | 0 | 197 | 319 MiB | 0 |
| 1g | 2.0 | 50 | 715 | 72 | 178 | **227** | 0 | 195 | 139 MiB | 0 |
| 1g | 2.0 | 100 | 713 | 122 | 392 | 541 | 0 | 194 | 161 MiB | 0 |
| 1g | 2.0 | 400 | 712 | 443 | 1216 | 1441 | 0 | 193 | 326 MiB | 0 |

**Máximo sustentável**: ~**350 RPS** com 1 vCPU (c≈10–50) e ~**715–750 RPS** com 2 vCPU (c≈50).
A vazão é limitada pela CPU do app (≈100%/200%) e escala linearmente com `cpus`; a memória não
influi até a fila crescer: com c=400 a 512 MiB a fila de requisições em curso encheu o limite e
houve OOM; a 1 GiB, não.

## B — memória pesada (`_load?cpu_ms=10&mem_mb=32&hold_ms=200`)

Cada requisição em curso segura ~32 MiB por ~230 ms (a memória em uso ≈ c × 32 MiB + base).

| mem | cpus | c=4 | c=8 | c=16 | c=32 | c=64 |
|---|---|---|---|---|---|---|
| 512m | 1.0 | 17 RPS, p99 241, 249 MiB | 35 RPS, p99 284, 378 MiB | **OOM ×9**, 98% erro | — | — |
| 512m | 2.0 | 18 RPS, p99 237 | 35 RPS, p99 236, 391 MiB | **OOM ×9**, 98% erro | — | — |
| 1g | 1.0 | 17 RPS, p99 286 | 35 RPS, p99 297 | 43 RPS, p99 494, 630 MiB | 61 RPS, 4,3% erro, **1 OOM**, 912 MiB | **OOM ×9**, 99% erro |
| 1g | 2.0 | 17 RPS, p99 239 | 35 RPS, p99 248 | 69 RPS, p99 255, 552 MiB | **OOM ×14**, 50% erro, 972 MiB | **OOM ×3**, 99% erro |

- Ponto de OOM: **512 MiB → entre 8 e 16 requisições simultâneas** (≈ 12 × 32 MiB); **1 GiB → ~32**.
  Dobrar a memória dobrou a concorrência suportada; `cpus` não muda o ponto de OOM (só a vazão antes dele).
- Depois do primeiro OOM o container entra em **laço de reinício** (9 reinícios em 60 s): cada
  reinício morre de novo com a fila acumulada e o gateway devolve `502`. O app fica praticamente
  indisponível enquanto a carga durar — o limite de memória protege os vizinhos, não o próprio app.

## C — CPU pesada (`_load?cpu_ms=100`)

| mem | cpus | c | RPS | p50 | p99 | erros | CPU app % |
|---|---|---|---|---|---|---|---|
| 512m | 1.0 | 4 / 16 / 64 | 9,7 / 9,7 / 9,7 | 403 / 1696 / 6804 | 497 / 3006 / 9097 | 0 | 98–99 |
| 1g | 1.0 | 4 / 16 / 64 | 9,2 / 9,3 / 9,2 | 409 / 1708 / 6691 | 508 / 3000 / 9614 | 0 / 0 / 1% | 96–98 |
| 512m | 2.0 | 4 / 16 / 64 | 19,6 / 19,6 / 19,6 | 202 / 811 / 3202 | 263 / 1390 / 4199 | 0 | 194–197 |
| 1g | 2.0 | 4 / 16 / 64 | 18,9 / 18,9 / 18,9 | 206 / 831 / 3576 | 267 / 1463 / 4488 | 0 | 195–199 |

Vazão = `cpus` × 1000 / `cpu_ms` (≈10 RPS por vCPU a 100 ms/req), exatamente como o limite de CPU
prevê; acima disso só cresce a fila (latência ∝ concorrência). Memória irrelevante aqui (~120 MiB).

## D — vizinho barulhento: impacto no hub

Sondas a 5 RPS cada (malha aberta, `--latency-correction`), apps com 1 GiB. Latência p50 / p99 em ms.
"3 apps" = os três saturados ao mesmo tempo (c=64 cada; C = `cpu_ms=100`, B = `mem_mb=32`).

| situação (cpus app) | `/applications` | `/admin` | `/api/admin/env` | `/_frag/servicos/dashboard` | CPU hub méd/máx | mem hub pico | erros hub |
|---|---|---|---|---|---|---|---|
| linha de base (1.0) | 0,7 / 1,7 | 0,8 / 1,8 | 0,6 / 1,2 | 1,1 / 2,4 | 1,3% / 2,9% | 4 MiB | 0 |
| ativos C (1.0) | 0,7 / 3,7 | 0,7 / 1,2 | 0,5 / 2,3 | 1,2 / 2,8 | 1,5% / 3,3% | 9 MiB | 0 |
| ativos B, OOM ×9 (1.0) | 0,7 / 2,8 | 0,7 / 1,6 | 0,5 / 3,8 | 1,0 / 8,6 | 2,1% / 7,7% | 19 MiB | 0 |
| 3 apps C (1.0) | 0,7 / 1,5 | 0,7 / 3,0 | 0,5 / 0,8 | 1,1 / 78 ¹ | 3,1% / 9,3% | 22 MiB | 0 |
| 3 apps B, OOM ×27 (1.0) | 0,7 / 8,5 | 0,7 / 16 | 0,5 / 6,9 | 3161 / 42511 ¹ (52% erro) | 4,7% / 28,5% | 38 MiB | 0 |
| linha de base (2.0) | 0,7 / 1,3 | 0,7 / 1,1 | 0,5 / 1,5 | 1,0 / 1,6 | 1,1% / 2,8% | 4 MiB | 0 |
| ativos C (2.0) | 0,7 / 2,8 | 0,7 / 1,8 | 0,5 / 5,3 | 1,1 / 1,5 | 2,3% / 4,9% | 10 MiB | 0 |
| ativos B, OOM ×9 (2.0) | 0,7 / 7,3 | 0,7 / 5,0 | 0,5 / 6,4 | 1,0 / 3,2 | 2,7% / 10% | 19 MiB | 0 |
| 3 apps C (2.0: 6 vCPU pedidas em 4) | 0,7 / 3,2 | 0,7 / 5,4 | 0,5 / 3,5 | 6 / 39 ¹ | 4,5% / 5,5% | 24 MiB | 0 |
| 3 apps B, OOM ×27 (2.0) | 0,7 / 29 | 0,7 / 16 | 0,5 / 38 | 2420 / 30031 ¹ (34% erro) | 4,8% / 20,6% | 39 MiB | 0 |
| linha de base depois (2.0) | 0,7 / 1,1 | 0,7 / 1,1 | 0,5 / 1,0 | 1,0 / 2,3 | 1,4% | 23 MiB | 0 |

¹ Nos cenários "3 apps", `servicos` também está sob carga (não está ocioso): a latência medida é a
do próprio app saturado/reiniciando, não do hub.

- **Rotas do hub nunca falharam** e o p50 não mudou (0,5–0,8 ms); o pior p99 foi 38 ms (máx. 72 ms),
  com os três apps em laço de OOM e a CPU pedida (6 vCPU) acima das 4 disponíveis.
- Um app saturado **não afeta o app ocioso** (`servicos` p99 ≤ 8,6 ms com `ativos` em CPU ou OOM).
- Hub sob carga: ≤ 12% de CPU na média (A a 750 RPS pelo gateway: ~24%), pico de memória 65 MiB
  (A, c=400) — muito abaixo de 1 vCPU / 1 GiB. Nenhum reinício do hub-web em todos os testes.

## Conclusões e recomendações para a VM de destino

1. **Memória por app: 1 GiB como padrão; 512 MiB só para apps pequenos e com concorrência limitada.**
   O ponto de OOM é ≈ memória / (memória por requisição em curso). Com 512 MiB o app de teste caiu
   com 16 requisições pesadas simultâneas (e até na carga leve com fila de 400); com 1 GiB aguentou o
   dobro. Suba para **2 GiB** quando: o runtime tem base alta (JVM, Node com SSR, Python com pandas —
   base ≥ 400 MiB), o app processa uploads/relatórios (dezenas de MiB por requisição) ou o
   `docker stats` em produção mostrar pico > 70% do limite. Como o OOM vira laço de reinício, o app
   deve também **limitar a concorrência** interna (fila/semáforo) para não passar do limite.
2. **CPU por app: `cpus: 1.0` como padrão** (≈350 RPS de fragmentos leves por app com p99 < 100 ms,
   muito além do uso interno esperado); **2.0** só para apps CPU-bound (relatórios, exportações), pois
   a vazão dobra exatamente (C: 9,5 → 19 RPS). O padrão atual do manifesto (0,5 vCPU) serve para apps
   quase ociosos; o limite é teto, não reserva, então somar tetos acima de 4 é aceitável.
3. **Reserva do hub**: manter `cpus 1.0`, `1 GiB` e `cpu_shares 2048`. Foi suficiente com folga
   (máx. ~24% CPU, ~65 MiB) e o peso dobrado manteve o p99 do hub < 40 ms com 6 vCPU pedidas em 4.
   Poderia descer para 512 MiB, mas 1 GiB custa pouco e cobre picos de SSE/logs.
4. **Quantos apps cabem numa VM de destino** (8 GB): reservando ~1 GiB para o SO e 1 GiB para o hub, sobram ~6 GiB
   → **5 apps de 1 GiB com folga** (6 no limite, sem sobra para deploy azul/verde, que mantém o
   `-prev` rodando durante a troca) ou ~10 apps de 512 MiB. Em CPU, 4 vCPU compartilhadas comportam
   5–6 apps com `cpus 1.0` desde que no máximo 2–3 fiquem saturados ao mesmo tempo.
5. **Quando migrar**: para um CX43 (8 vCPU / 16 GB) quando houver > 5 apps de 1 GiB, qualquer app que
   precise de 2 GiB junto com outros 4, ou quando a CPU média do host passar de ~60% no horário de
   pico (vCPU compartilhada perde desempenho com *steal*). Para **múltiplos nós** quando for preciso
   isolar falhas de host (uma única VM de destino é ponto único de falha), quando um app sozinho precisar de
   > 2 vCPU sustentadas, ou para dedicar vCPU (linha CCX) a apps sensíveis a latência.

## Orçamento da VM de destino com as dependências dos apps (2026-09-27)

`docker-compose.vm-destino.yml` agora inclui banco, object store e API simulada. Limites (APP_MEM=1g):

| Serviço | CPU | Memória | Observação |
|---|---|---|---|
| hub-web | 1.0 (shares 2048) | 1 GiB | gateway em streaming: ~3 MiB durante download de 150 MB |
| hub-db | 1.0 | 1 GiB | Postgres 16, `shared_buffers` 256 MB; ~660 MiB após seed + relatórios |
| hub-objstore | 0.5 | 512 MiB | MinIO, ~90 MiB |
| hub-extapi | 0.25 | 64 MiB | ~1 MiB |
| hub-app-ativos | 1.0 | 1 GiB | importação de 250 mil linhas (39 MB) com pico de ~36 MiB |
| hub-app-fornecedores | 0.5 | 512 MiB | |
| hub-app-servicos | 1.0 | 1 GiB | |
| hub-seed / hub-objstore-init | 0.5 | 128 MiB cada | só na partida |
| **Total** | | **≈ 5,6 GiB** (+0,25 transitório) | ~2 GiB para SO e cache de páginas |

O próximo teste de carga deve usar a mistura realista (listas, edições, importação, exportações,
relatório, anexos) em vez de `/_load`.

## E — teste de caos: queda do app no meio da importação e de uma rajada de gravações (2026-09-27)

Roteiro `loadtest/chaos/chaos.mjs` (detalhes e linha do tempo: [`results/2026-09-27/chaos.md`](results/2026-09-27/chaos.md)),
compose da plataforma no ar, hub-sdk nos apps (tarefas duráveis, `Idempotency-Key`, outbox):

- importação de 250 mil linhas (38,7 MB; 247.548 válidas) + 300 cadastros espaçados + 2 ondas de 60
  cadastros concorrentes; `docker kill hub-app-ativos` com 24% e 65% da planilha lida (com 60
  requisições em voo em cada queda); clientes repetem com a MESMA chave em erro de rede/502/503/504.
- **Passou em todas as verificações**: importação concluída pelo checkpoint (3 tentativas = 1 + 2
  retomadas após o lease de 15 s vencer), 247.548 linhas gravadas == válidas, zero duplicados, zero
  lote parcial (gravadas + rejeitadas = 250.000 lidas); 420 cadastros → 420 ativos (107 precisaram de
  nova tentativa; 12 já tinham sido efetivados antes da queda e receberam "operação já concluída", 1
  recebeu a resposta guardada); 422 eventos de auditoria no outbox do app → 422 na trilha do hub, uma
  vez cada, todos confirmados. Tempo total ~2 min (a importação levou ~50 s com as duas quedas).
- Achados do caminho: (1) `docker kill` não dispara a restart policy `unless-stopped` (o roteiro
  religa com `docker start`; um OOM kill real reinicia sozinho); (2) com um único pool por app, uma
  onda de requisições idempotentes esgotava as conexões (o middleware segura uma e o handler pede
  outra) e atrasava o heartbeat do lease — corrigido com pools separados para idempotência e
  tarefas (`HUB_IDEM_POOL`, `HUB_JOBS_POOL`); (3) a checagem anti zip-bomb da onda 1 recusava
  planilhas de 250 mil linhas (aba > 300 MB descomprimida) — a aba, lida em streaming, agora tem
  teto próprio de 2 GiB (a razão de compressão continua valendo).

### E2 — caos com queda do hub e do banco (2026-09-27, após a revisão da onda 2)

`node loadtest/chaos/chaos.mjs --rows 250000 --burst 300 --kills 2 --hub-kills 1 --db-restarts 1`
(o relatório [`results/2026-09-27/chaos.md`](results/2026-09-27/chaos.md) foi regravado com esta execução):
`docker kill hub-app-ativos` com 24% e 81% lidas, `docker kill hub-web` com 41% (coleta do outbox
interrompida) e `docker kill hub-db` com 82% (Postgres cai no meio de um lote). **Passou em todas as
14 verificações**: importação concluída (247.548 == válidas, sem duplicados nem lote parcial, 3
tentativas); 420 cadastros → 420 ativos (411 criados, 9 "já concluída"); 422 eventos do outbox → 422
na trilha do hub, uma vez cada, 0 pendentes no app e 0 aguardando confirmação no registro do hub
(compactado). Tempo total ~2,3 min.

## Onda 4 — implantação sem indisponibilidade sob carga (2026-09-27)

Detalhe: [`results/2026-09-27/zero-downtime.md`](results/2026-09-27/zero-downtime.md) (`node loadtest/zero-downtime/zero-downtime.mjs --rps 200`).
Compose + `docker-compose.vm-destino.yml`; `oha` a 200 RPS por 160 s pelo gateway (90% `/_frag/ativos/dashboard`,
10% lista `/_frag/ativos/assets?q=NT-1`), sonda de 20 RPS com carimbo de tempo e uma exportação CSV de
1 milhão de linhas (274 MB) lida devagar desde antes da 1ª troca; 3 implantações de produção do `ativos`
(upload → aprovação → blue/green), 1 rollback e 1 implantação em staging.

| Métrica | Resultado |
|---|---|
| Requisições oha | 32.002 a 200,0 RPS — **0 respostas não-2xx**; 2 requisições canceladas pelo próprio `oha` no fim do `-z` ("aborted due to deadline") |
| Latência oha p50 / p95 / p99 / máx | 1,3 / 7,1 / 62,3 / 212,7 ms |
| Sonda (2.632 req.) | **0 não-2xx**; p99 nas janelas de troca (−2 s…+5 s): 86 / 39 / 27 ms (deploys) e 10 ms (rollback) |
| Exportação longa | **concluída** (HTTP 200, 1.000.001 linhas) atravessando as trocas; a 1ª troca esperou 33,8 s de drenagem até o container antigo terminar de enviar |
| Duração das implantações | 202 em ≤ 20 ms; job 35 s (com a drenagem da exportação), 1,2 s e 1,3 s; rollback via `-prev` 0,8 s |
| Staging durante a carga | implantado; container de produção intocado; 0 não-2xx na janela |

Observação: uma 1ª execução com a lista **sem filtro** (`count(*)` em 300 mil linhas, ~0,8 s cada) saturou o
`hub-db` (1 vCPU) em ~13 RPS — timeouts em qualquer instante, com ou sem troca; por isso a lista da carga é a
busca paginada. Mede-se a troca de container, não a capacidade do banco.

### Reexecução após as correções da revisão (2026-09-27)

Mesmo roteiro, agora com **imagens de fato diferentes** por versão (`docker build --build-arg
APP_VERSION=<v>`: 4 digests distintos), **gravações POST** com `Idempotency-Key` (4/s, cada chave
reenviada logo em seguida) durante as trocas e **verificação de qual versão atendeu** cada requisição
(`x-app-version`). Detalhe em [`results/2026-09-27/zero-downtime.md`](results/2026-09-27/zero-downtime.md).

| Métrica | Resultado |
|---|---|
| Requisições oha | 32.001 a 200,0 RPS — **0 respostas não-2xx, 0 erros de conexão/tempo**; 1 requisição cancelada pelo próprio `oha` no fim do `-z` (em curso quando a carga acabou — não é resposta do hub) |
| Latência oha p50 / p95 / p99 / máx | 1,3 / 7,9 / 74,6 / 243,5 ms |
| Sonda (3.873 req.) | **0 não-2xx**; p99 nas janelas de troca: 167 / 81 / 48 ms (deploys) e 43 ms (rollback) |
| Gravações POST | 1.244 (622 cadastros + 622 repetições da mesma chave) — **0 não-2xx**; **622/622** repetições respondidas do registro (201 + `idempotent-replay`) |
| Virada por versão | nas 4 trocas, **0** respostas da versão antiga começaram depois da primeira da nova (virada limpa, sem ida e volta); **0** respostas da versão de staging em produção |
| Exportação longa | **concluída** (HTTP 200, 1.000.001 linhas, 274 MB) atravessando as 4 trocas; a 1ª troca drenou 35,2 s |
| Duração das implantações | 202 em ≤ 32 ms; jobs 36,4 s (com a drenagem), 1,3 s e 1,2 s; rollback via `__prev` 0,9 s |
| Staging durante a carga | implantado **com banco próprio de staging**; container de produção intocado; 0 não-2xx na janela |

E2E das correções (compose, `node loadtest/e2e/w4-fixes.mjs`): staging sem banco próprio recusado;
staging só em `hub-deps-ativos__staging` e sem a credencial de produção; link GET e `fetch` não trocam
de ambiente, o botão troca (fragmento com a versão de staging), `/api` bloqueada no modo staging;
renomeação injetada (`HUB_FAULT_RENAME_TO=__prev`) → implantação "desfeita", mesmo container
atendendo, upstream persistido pelo nome, 0 não-2xx em 89 requisições da sonda; `docker kill hub-web`
drenando uma exportação → na volta a reconciliação removeu o `__next`, marcou a implantação como
"Interrompida", manteve o anterior pelo nome estável e o job seguiu consultável.

## F — onda 5 com as correções da revisão (2026-09-27)

- **DR** ([`results/2026-09-27/dr.md`](results/2026-09-27/dr.md)): DROP DATABASE `ativos` (1,79 M linhas), bucket `fornecedores` esvaziado e dados dos apps apagados → restauração pela API com a trava do app, **"Em manutenção (restauração)"** visível (reiniciar/backup → 409 durante), **snapshot de segurança** antes (registro "Pré-restauração"; o banco apagado vira nota), banco atual posto de lado e apagado só no fim, credenciais das tarefas fora do env (85 `docker inspect` sem segredo). md5 das linhas, anexos e arquivos idênticos; **RTO ≈ 57 s** (restauração em si 51,8 s).
- **OOM** ([`results/2026-09-27/oom.md`](results/2026-09-27/oom.md)): 256 MiB pelo **caminho da UI** (Limites + "Aplicar"; admission control com a capacidade do engine: 16 vCPU · 23,3 GiB) → 4 OOM, Degradado, policy `no`; com o app ainda degradado, 1 GiB + "Aplicar" recriou o container → a supervisão devolveu `no` ao container novo → 60 s estável → alerta "recuperado", Disponível, `unless-stopped`, HTTP 200.
- **Staging** ([`results/2026-09-27/staging-fixes.md`](results/2026-09-27/staging-fixes.md)): link de staging repassado a um navegador anônimo ou a outra sessão do mesmo usuário não abre o staging (e invalida o ticket); logout no host principal → a sessão de staging responde 401 na requisição seguinte.

## Onda 6 — suíte de validação (2026-09-27)

Dados brutos: [`results/2026-09-27/onda6/`](results/2026-09-27/onda6/). Suíte completa
(`bash loadtest/suite.sh`, compose padrão recriado do zero): [`suite.md`](results/2026-09-27/onda6/suite.md) —
**7/7 passos OK** (E2E, troca sem indisponibilidade, DR, paridade, ZAP, `cargo audit`, `cargo deny`).

| Item | Resultado |
|---|---|
| E2E Playwright (`tests-e2e`, 27 cenários: 26 do roteiro + várias abas) | **27/27** localmente (2,9 min) e **27/27, 0 flaky** no GitHub Actions (execução 36325120585: compose no runner, CI → hub e suíte em 2,4 min) |
| Várias abas (5 abas, um contexto) | **1** conexão SSE no servidor com 3 abas em "Tempo real" (`/api/admin/logs/stats`: 0 → 1), failover para outra aba ao fechar a líder (continua 1), 409 entre abas, 0 conexões ao fechar tudo |
| Troca sem indisponibilidade ([`zero-downtime.md`](results/2026-09-27/onda6/zero-downtime.md)) | 32.001 req a 200 RPS, **0 não-2xx**, p50/p95/p99 1,2/3,0/12,7 ms; sonda 3.890 req, 0 não-2xx; 1.254 POSTs idempotentes (627 repetições respondidas do registro); virada limpa nas 4 trocas; exportação de 137 MB atravessou 2 trocas |
| DR ([`dr.md`](results/2026-09-27/onda6/dr.md)) | banco `ativos` (498 mil linhas) + bucket + arquivos destruídos → restaurados com md5/sha256 idênticos; **RTO 19,8 s** (restauração 15,1 s); manutenção visível e bloqueando (409); snapshot de segurança |
| Paridade `hub dev` × produção ([`parity.md`](results/2026-09-27/onda6/parity.md)) | **idêntico** em 19 verificações (5 shells, 5 fragmentos, 8 assets byte a byte, consulta externa) |
| CI → hub ([`ci-publish.md`](results/2026-09-27/onda6/ci-publish.md)) | token de API pela API → `hub pack` → `POST /api/ops/packages` (Bearer) 201 → aprovação → blue/green → `x-app-version` = versão nova |
| OWASP ZAP baseline autenticado ([`zap/`](results/2026-09-27/onda6/zap/)) | 0 FAIL; achados reais corrigidos (CSP ausente em respostas antecipadas, páginas autenticadas armazenáveis, sem Permissions-Policy/CORP); restantes aceitos (ver PENDENCIAS) |
| `cargo audit` / `cargo deny` | 0 vulnerabilidades e 0 avisos em 425 crates; advisories/bans/licenses/sources ok (22 avisos de versões duplicadas) |

### Carga realista (perfil vm-destino)

`node loadtest/realistic.mjs run --mem <512m|1g> --fresh` (+ `--db-cpus 2`): VUs com pausa de 2–6 s,
mistura 60% lista/busca · 20% detalhe/edição (Idempotency-Key) · 8% exportação · 5% relatório pesado ·
5% anexo · 2% importação de 5 mil linhas; 1 vCPU por app, hub 1 vCPU/1 GiB, hub-db 1 vCPU/1 GiB
(padrão do compose), MinIO 0,5 vCPU. SLO: **p95 < 500 ms em lista e detalhe, erros < 1%, sem OOM**.
Resultados: [`realistic-mem1g.md`](results/2026-09-27/onda6/realistic-mem1g.md),
[`realistic-mem512m.md`](results/2026-09-27/onda6/realistic-mem512m.md),
[`realistic-mem1g-db2cpu.md`](results/2026-09-27/onda6/realistic-mem1g-db2cpu.md).

**Usuários simultâneos por app dentro do SLO** (quebra no passo seguinte):

| Cenário | 512 MiB | 1 GiB | 1 GiB + hub-db 2 vCPU | O que quebra primeiro | Gargalo |
|---|---|---|---|---|---|
| `ativos` (busca em 500 mil ativos, editor, XLSX, import) | **25** | **25** | **75** | lista p95 510–540 ms (35 usuários) | **CPU do hub-db** (100% de 1 vCPU; app 8–13%) |
| `fornecedores` (diretório, ficha, PDF, anexos no S3) | **800** | **800** | — | detalhe/anexo p95 ≈ 5 s, 1,4% erros (1.200) | **CPU do MinIO** (≈ 41% = limite de 0,5 vCPU); app 11–13% |
| `servicos` (2 M chamados, relatório gerencial) | **200** | **200** | — | lista p95 0,8–1,2 s (300) | **CPU do hub-db** (86%/105%); 429 do semáforo de relatórios sobe até 9–11% |
| os três ao mesmo tempo | **50** | **50** | **150** | detalhe p95 ≈ 660 ms (75) | **CPU do hub-db** |

- **Memória não é o limite**: pico por app de 33–70 MiB (`ativos`, `servicos`) e até 315 MiB no
  `fornecedores` com 1.200 usuários enviando anexos; nenhum OOM nem reinício em nenhum passo — 512 MiB e
  1 GiB dão o mesmo resultado. O `hub-db` usa ~550–980 MiB (cache de páginas) do seu 1 GiB.
- **Hub quase ocioso**: p99 da sonda em `/applications` ≤ 43 ms em todos os passos dentro do SLO
  (≤ 80 ms mesmo quebrando), CPU média do hub-web ≤ 21% (pico 36%) e memória ≤ 235 MiB, com até 300
  req/s passando pelo gateway.
- **Proteção por projeto (429)**: o semáforo de operações pesadas (`HUB_HEAVY_MAX`) recusa exportações e
  relatórios em excesso em vez de derrubar o app. No `servicos` a partir de ~75 usuários 3–11% das
  requisições (quase todas relatórios/CSV) recebem 429 — dentro do SLO, mas para relatórios o limite
  prático é **~50 usuários** (429 ≤ 2%). No `fornecedores` o 429 começa perto de 800.
- **Conexões do Postgres**: até 37 ativas (limite de 40 por app no provisionamento).
- **Dobrar a CPU do banco triplica o `ativos`** (25 → 75) e o cenário combinado (50 → 150): o hub-db
  chegou a 150–209% de CPU; o app continuou em 16–27%.

**Recomendação para a VM de destino (4 vCPU / 8 GB)**: a plataforma cabe folgada (hub ≤ 0,4 vCPU e ≤ 250 MiB em
pico); o recurso escasso é o **Postgres compartilhado**. Dê **2 vCPU** ao `hub-db` (tirando do teto dos
apps, que usam ≤ 0,3 vCPU cada nesses perfis) e mantenha os apps em **512 MiB** (sobra memória para o
cache do Postgres: `shared_buffers`/`effective_cache_size` maiores). Capacidade esperada nesse
arranjo: ~75 usuários ativos no `ativos`, ~200 no `servicos` (relatórios pesados: ~50 simultâneos),
~800 no `fornecedores` (MinIO com 1 vCPU se os anexos forem o uso principal) e ~150 usuários
distribuídos entre os três. **Quando escalar**: CPU do `hub-db` acima de 70% sustentado ou p95 de lista
acima de 300 ms → Postgres dedicado (VM de destino só para o banco, ou gerenciado) antes de mexer nos apps;
429 acima de 2% em relatórios → aumentar `HUB_HEAVY_MAX` junto com a CPU do banco (o relatório é
consulta pesada no banco); anexos lentos → mais CPU para o MinIO ou object store externo. Ressalvas do
ambiente (Docker Desktop, gerador na mesma VM) valem aqui também: validar na máquina real.

## Onda 7 — stack Python (FastAPI) oficial: caos, carga e dimensionamento (2026-09-27)

Dados brutos: [`results/2026-09-27/onda7/`](results/2026-09-27/onda7/),
[`chaos-contratos.md`](results/2026-09-27/chaos-contratos.md),
[`w7-oom-512m-w1-process.md`](results/2026-09-27/w7-oom-512m-w1-process.md),
[`w7-oom-512m-w2-inline.md`](results/2026-09-27/w7-oom-512m-w2-inline.md),
[`conformance.md`](results/2026-09-27/conformance.md), [`parity-contratos.md`](results/2026-09-27/parity-contratos.md).
App: `contratos` (FastAPI + hub-sdk-python, Python 3.12, uvicorn), 200 mil contratos no Postgres
`app_contratos`. Mesmo perfil vm-destino da onda 6 (`docker-compose.vm-destino.yml`: apps com 1 vCPU,
`hub-db` com 2 vCPU, cpuset 0-3), mesma mistura realista e mesmo SLO (p95 < 500 ms em lista e
detalhe, erros < 1%, sem OOM). Banco recriado do zero (`--fresh`) antes das rampas.

### Caos (exatamente uma vez, nenhuma auditoria perdida)

`node loadtest/chaos/chaos-contratos.mjs` (importação de 250 mil linhas + 420 cadastros com
`Idempotency-Key`; `docker kill` do app 2×, do hub-web 1× e do hub-db 1×) — **passou em 15/15
verificações**, com as tarefas no mesmo processo (primeira execução) e com o processo de tarefas
separado (execução final): 247.500 contratos gravados = linhas válidas, 0 duplicados, contadores do
checkpoint = banco, 2.500 linhas no relatório de erros sem duplicar, 420/420 cadastros aplicados uma
vez (2–4 respondidos pelo registro de idempotência como "já concluída"), 422 eventos do outbox
todos na trilha do hub uma vez, 0 pendentes. Pico de memória do container: **296–335 MiB** de 1 GiB.

### Memória e OOM (importações grandes, 512 MiB)

| Cenário (2 importações simultâneas de 250 mil linhas, 13,5 MB cada) | Pico | OOM | Resultado |
|---|---|---|---|
| `UVICORN_WORKERS=1`, **`HUB_JOBS_MODE=process`** (padrão da imagem) | **288 MiB** | 0 | as duas concluídas na 1ª tentativa (uma planilha carregada por vez) |
| `UVICORN_WORKERS=2`, tarefas no web (`inline`) | 431 MiB | **2** (processo uvicorn morto pelo kernel; o container ficou de pé) | concluídas em 3 e 2 tentativas, **sem duplicado** (lease vencido → retomada do checkpoint) |

O python-calamine carrega a aba em memória nativa (~0,65 KiB por linha de 12 colunas: ~130 MiB por
200 mil linhas); por isso o app só carrega uma planilha por processo por vez e limita a aba a 300 MiB
descomprimidos. Com vários processos rodando tarefas, cada um carrega a sua — a 512 MiB isso
estourou. Com o processo de tarefas separado, um OOM mata só ele (o web continua respondendo) e o
supervisor o religa.

### Tarefas no mesmo processo × processo separado (achado da onda 7)

Com as tarefas no processo do web, uma exportação XLSX (openpyxl, CPU em Python) disputa o GIL
com as requisições: uma página trivial foi de **4 ms para 60 ms (p50)**, a lista de 48 para 107 ms.
Com `HUB_JOBS_MODE=process` a página trivial ficou em **4 ms** durante a mesma exportação. Na carga
realista isso é a diferença entre **30** e **~100** usuários no SLO (tabela abaixo).

### Usuários simultâneos dentro do SLO (1 vCPU por app, hub-db 2 vCPU)

| Cenário | Máx. no SLO | Quebra | Pico de memória do app | CPU do app / do banco no máximo | Observação |
|---|---|---|---|---|---|
| `contratos` 1 GiB, 1 processo web, **tarefas em processo** | **75–100** | 100–150 | 194 MiB | 56–62% / 62–73% | duas rampas: 100 OK (lista p95 417 ms) e 100 no limite (615 ms) |
| `contratos` **512 MiB**, 1 web, tarefas em processo | **100** | 150 | 195 MiB | 60–75% / 57–66% | 512 MiB bastam para esse perfil (sem OOM, 0 reinícios) |
| `contratos` 1 GiB, **2** processos web, tarefas em processo | **100** | 150 | 274 MiB | 45–68% / 68–70% | 2 web num vCPU não aumentou a capacidade; +80 MiB; a 150 faltaram conexões (12,6% de erros) — corrigido, ver abaixo |
| `contratos` 1 GiB, 1 web, **tarefas inline** | **30** | 40 | 108 MiB | 37–49% / 32–41% | exportações XLSX no mesmo GIL que o HTTP |
| `ativos` (Rust) 1 GiB — referência na mesma sessão | **50** | 75 | 52 MiB | 14–24% / **102–144%** | limitado pelo Postgres (busca em 300 mil ativos com 30 colunas) |
| **mix** (3 Rust + contratos, VUs divididos) | **50** | 75 | 41/5/2/117 MiB | ≤ 32% / 62–94% | o Postgres compartilhado volta a ser o gargalo |

- **Exportação XLSX** (tarefa, openpyxl write-only) é a operação mais cara do app Python: p95 de
  4–5 s com poucos usuários e 38–53 s a 75–100 usuários (fila do semáforo de operações pesadas no
  processo de tarefas; CSV em streaming 1–3 s). No Rust o XLSX do `ativos` fica em ~1 s.
- **Orçamento de conexões** (achado): o papel `app_contratos` tem `CONNECTION LIMIT 40`; com 2
  processos web + o de tarefas os pools padrão (12 + 12 + 5 por processo) passaram de 40 e 12,6%
  das requisições falharam com *too many connections*. O SDK agora divide os pools do web por
  `UVICORN_WORKERS` e o processo de tarefas abre só 9 conexões (padrão 35 ≤ 40); reexecutado a 100 e
  150 usuários com 2 web: **0 erros de conexão** (latência alta nessa reexecução porque o banco já
  tinha ~1 milhão de contratos depois do caos e das sondas de OOM — não usar para capacidade).
- Hub: p99 da sonda ≤ 14 ms em todos os passos; hub-web ≤ 6% de CPU.

### Recomendação de workers (uvicorn) e memória para apps Python

- **1 processo web por vCPU** (`UVICORN_WORKERS` = vCPUs do manifesto) **+ tarefas em processo
  separado** (`HUB_JOBS_MODE=process`, padrão da imagem). Com 1 vCPU, 2 processos web não ganham
  capacidade (a CPU é a mesma) e custam ~80 MiB e conexões.
- **Memória**: 512 MiB bastam para o tráfego medido (pico 195 MiB a 100 usuários) **com uma planilha
  grande por vez**; deixe ~300 MiB de folga por planilha de 200–250 mil linhas (ou reduza
  `CONTRATOS_IMPORT_MAX_SHEET_MB`). Com importações grandes frequentes ou mais processos, 1 GiB.
- Base ociosa: Python ~95–110 MiB (web + tarefas) × Rust 2–40 MiB por app.

### Dimensionamento da VM de destino (4 vCPU / 8 GB) misturando Rust e Python

| Item | Rust (por app) | Python (por app, web + tarefas) |
|---|---|---|
| Limite recomendado | 256–512 MiB · 1 vCPU (teto) | 512 MiB (1 GiB com importações grandes frequentes) · 1 vCPU |
| Uso típico medido | 20–60 MiB, ≤ 0,3 vCPU | 100–200 MiB, 0,5–0,75 vCPU a ~100 usuários |

Com o orçamento fixo da plataforma (hub 1 GiB, hub-db 1 GiB + cache, MinIO 512 MiB, ~1 GiB para o
SO/cache de páginas) sobram ~4,5 GiB de limites para apps: **~6 apps** mistos com folga (ex.: 3 Rust
a 512 MiB + 3 Python a 512 MiB = 3 GiB; ou 4 Rust + 2 Python com 1 GiB) — os limites de CPU já são
sobrecompromisso proposital (tetos, não reservas). A capacidade de **usuários** continua definida
pelo **Postgres compartilhado** (2 vCPU): ~50 usuários simultâneos distribuídos entre os quatro apps
de demonstração, ~100 num único app Python leve, ~50 no `ativos`. Acima disso: Postgres dedicado
antes de mais apps. Números indicativos (Docker Desktop, gerador na mesma VM; ver ressalvas).

## Onda 8 — custo da observabilidade no hub, DR com backups cifrados e conformidade

**Custo da instrumentação** (spans do gateway/ações/jobs, `traceparent` + `x-hub-flags` por
requisição, métricas RED/saturação, coleta de `/_hub/telemetry` a cada 2 s, sondas por minuto,
`docker stats` a cada 15 s, avaliação de flags). Mesma sessão, mesmo compose vm-destino
(`docker-compose.vm-destino.yml`, apps 1 GiB, hub-db 2 vCPU), cenário **mix** (3 apps Rust + contratos),
passos de 45 s, com `HUB_TELEMETRY=1` (padrão) e `HUB_TELEMETRY=0` (desligada) —
[`realistic-mix-telemetria-on.md`](results/2026-09-28/onda8/realistic-mix-telemetria-on.md),
[`-on-2`](results/2026-09-28/onda8/realistic-mix-telemetria-on-2.md) (repetição do passo 50/75) e
[`-off`](results/2026-09-28/onda8/realistic-mix-telemetria-off.md):

| Passo (usuários) | CPU hub (média) ligada / desligada | Memória hub (pico) ligada / desligada | p99 da sonda do hub ligada / desligada | Capacidade (SLO) |
|---|---|---|---|---|
| 25 | 2% / 1% | 59 MiB / 47 MiB | 6,3 / 4,7 ms | ✅ / ✅ |
| 50 | 3–4% / 3% | 62–65 MiB / 50 MiB | 26–41 / 46 ms | ✅ (repetição) / ✅ |
| 75 | 6% / 5% | 69 MiB / 53 MiB | 51 / 39 ms | ❌ / ❌ (hub-db 110–145% de CPU) |

- **Custo: ~+1 ponto percentual de CPU e +12–16 MiB de memória** no hub (buffer de traces de até 20 mil
  spans + agregados). O hub continua a **~6% de 1 vCPU e < 70 MiB de 1 GiB** no passo mais alto —
  folga de sobra. A capacidade do cenário combinado não mudou (**50 usuários**, como na onda 7): quem
  quebra o SLO em 75 continua sendo o **Postgres compartilhado**. Na primeira rampa com a instrumentação
  ligada o passo 50 quebrou por pouco (lista p95 477 ms / detalhe 598 ms com hub-db a 75%); a repetição
  passou (366/453 ms) — variação do banco, não do hub (p99 da sonda 26 ms).
- Latência do gateway: sem diferença mensurável (p99 da sonda na mesma faixa nas duas rodadas).

**DR com backups cifrados** ([`dr.md`](results/2026-09-28/onda8/dr.md)): agenda de 1 min → desastre
(DROP DATABASE, bucket e arquivos apagados) → restauração pela API: 701 071 linhas com md5 idêntico, 20
anexos e 6 arquivos byte a byte, apps respondendo, manutenção e snapshot de segurança como antes; **106
partes cifradas em 4 pacotes e nenhuma parte em texto claro** no volume depois da restauração.
Migração do cofre na partida sobre o volume real da onda 7: 10 segredos do `admin.json` cifrados, original
guardado cifrado; trilha de auditoria migrada para a cadeia.

**Conformidade** ([`conformance.md`](results/2026-09-28/onda8/conformance/conformance.md)): 4/4 com os
itens novos (propagação hub → app → banco, `/_hub/telemetry`, `/_hub/flags`, UI por `x-hub-flags`) —
achou e fez corrigir o SDK Rust aceitando ambiente inválido em `/_hub/flags`.

**Perfil `observability`** (OTLP → Collector → Jaeger): com `OTEL_EXPORTER_OTLP_ENDPOINT=http://hub-otel-collector:4318`
o Jaeger lista os serviços `hub`, `ativos`, `servicos` e `contratos` e mostra os spans dos apps como
`CHILD_OF` do span do hub. Memória medida em repouso após tráfego: Collector ~29 MiB (teto 192 MiB),
Jaeger ~13 MiB (teto 256 MiB, cresce com os traces guardados em memória) — opcional na VM de destino.

## Onda 9 — custo da experiência (analytics, busca federada, Inbox) no hub

Mesmo compose vm-destino (`docker-compose.vm-destino.yml`, apps 1 GiB, hub-db 2 vCPU), cenário **mix**, passos de
45 s: **A** = `HUB_ANALYTICS=1` + `--ux` (10% das ações são busca federada `/api/me/search` com
fan-out para os 4 apps, 5% Inbox, 5% Início, navegação com `x-hub-nav` contada no analytics e uma
conexão SSE da Inbox a cada 5 usuários) × **B** = `HUB_ANALYTICS=0` sem tráfego de experiência —
[`realistic-mix-ux-on.md`](results/2026-09-28/onda9/realistic-mix-ux-on.md) e
[`-off`](results/2026-09-28/onda9/realistic-mix-ux-off.md).

| Passo | CPU hub A / B | Memória hub (pico) A / B | p99 sonda do hub A / B | busca p95 | Inbox p95 | Início p95 | SLO A / B |
|---|---|---|---|---|---|---|---|
| 25 | 2% / 3% | 100 / 99 MiB | 8,6 / 7,7 ms | 354 ms | 64 ms | 5 ms | ✅ / ✅ |
| 50 | 3% / 3% | 103 / 102 MiB | 18,4 / 7,8 ms | 330 ms | 5 ms | 15 ms | ✅ / ✅ |

- **Custo no hub: dentro do ruído** — CPU igual (3% de 1 vCPU), +1 MiB de memória (agregados do analytics
  e o estado da Inbox), p99 da sonda de 18 ms no passo 50 (A) contra 8 ms (B) — ambos longe do limite. O
  hub segue a ~3% de 1 vCPU e ~100 MiB de 1 GiB.
- **Busca federada**: p95 ~330–350 ms com 4 apps em paralelo (o mais lento manda; teto por app 800 ms),
  sem erros; o custo fica nos apps/banco (hub-db 74% × 56% no passo 50, pelas consultas de busca).
- Capacidade do cenário inalterada nos passos medidos (50 usuários dentro do SLO nas duas rodadas).
