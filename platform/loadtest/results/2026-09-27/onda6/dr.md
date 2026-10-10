# DR — recuperação de desastre (2026-09-27T13:57:28.253Z)

Compose no ar com o seed (`ativos`: banco `ativos` com 498680 ativos; `fornecedores`: bucket `fornecedores`).
Backups **agendados** (cron `* * * * *`, fuso America/Sao_Paulo) por containers de tarefa
(`pg_dump -Fc -Z 6`, `mc mirror`) na rede `hub-data`; restauração pela API (`POST /api/ops/backups/records/{id}/restore`, o botão "Restaurar").

## Números

| | ativos (banco + arquivos) | fornecedores (bucket + arquivos) |
|---|---|---|
| Backup agendado | banco ativos (28,9 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 28.9 MiB · 4.1 s | bucket fornecedores (4 objetos) · 2 arquivos · sem hooks no manifesto (crash-consistent) · 113 KiB · 0.7 s |
| Restauração (job: verificar → manutenção → parar → snapshot de segurança → banco de lado + pg_restore → mc → arquivos → religar + health) | 15.1 s | 3.7 s |

- **RTO medido** (do desastre até os dois apps respondendo, restaurações em paralelo): **19.8 s** (inclui o tempo de detecção deste script; a restauração em si: 15.1 s).
- **RPO**: igual ao intervalo da agenda — com `* * * * *` perde-se no máximo ~1 min (+ jitter configurado + duração do backup); com a política padrão `Diário 02:00` o RPO é de até 24 h. Execução perdida com o hub fora do ar: roda uma vez ao voltar (política `executar`).

## Integridade

| Verificação | Resultado |
|---|---|
| linhas + md5 da tabela assets | ✅ 498680 linhas, md5 6e9442a1b6b405cb9c3e33a7bf672d04 |
| tabelas do hub-sdk (hub_jobs) presentes | ✅ 1 |
| anexos byte a byte (sha256 pelo app) | ✅ 3 anexos |
| arquivos dos apps (sha256) | ✅ 5 arquivos |
| apps respondendo (ativos/assets e ficha do fornecedor) | ✅ HTTP 200 |
| "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps | ✅ {"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409}} |
| snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) | ✅ fornecedores |
| manutenção desligada no fim | ✅ desligada |
| credenciais fora do env dos containers de tarefa (docker inspect) | ✅ 19 inspeções, nenhum segredo |

## Linha do tempo

```
0.1 s — login ok
2.9 s — referência: assets 498680 linhas · 3 anexos · 5 arquivos de dados
2.9 s — políticas: a cada 1 min (cron "* * * * *", sem jitter)
42.1 s — agendado fornecedores: bucket fornecedores (4 objetos) · 2 arquivos · sem hooks no manifesto (crash-consistent) · 0.1 MiB em 0.7 s
45.1 s — agendado ativos: banco ativos (28,9 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 28.9 MiB em 4.1 s
45.1 s — agendas de volta para Manual (nada novo é copiado depois do desastre)
47.5 s — DESASTRE: DROP DATABASE ativos, bucket fornecedores vazio (0 anexos visíveis), dados dos apps apagados; ativos/assets → HTTP 200
50.1 s — fornecedores durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
50.2 s — ativos durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
51.2 s — restaurado fornecedores em 3.7 s: Restauração de Rede de Fornecedores concluída — snapshot de segurança fornecedores-1790517428529 (0 B) · bucket fornecedores (4 objetos) · 2 arquivos · hub-app-fornecedores religado e saudável em 0.0 s.
62.6 s — restaurado ativos em 15.1 s: Restauração de Gestão de Ativos concluída — snapshot de segurança: estado atual vazio (nada a guardar) · banco ativos restaurado · 3 arquivos · hub-app-ativos religado e saudável em 0.0 s.
62.6 s — snapshots de segurança registrados: fornecedores (snapshot de segurança antes de restaurar · bucket fornecedores (0 objetos))
64.6 s — manutenção depois: nenhuma · env dos containers de tarefa inspecionados: 19 · credenciais no env: não
67.7 s — OK  linhas + md5 da tabela assets — 498680 linhas, md5 6e9442a1b6b405cb9c3e33a7bf672d04
67.7 s — OK  tabelas do hub-sdk (hub_jobs) presentes — 1
67.7 s — OK  anexos byte a byte (sha256 pelo app) — 3 anexos
67.7 s — OK  arquivos dos apps (sha256) — 5 arquivos
67.7 s — OK  apps respondendo (ativos/assets e ficha do fornecedor) — HTTP 200
67.7 s — OK  "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps — {"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409}}
67.7 s — OK  snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) — fornecedores
67.7 s — OK  manutenção desligada no fim — desligada
67.7 s — OK  credenciais fora do env dos containers de tarefa (docker inspect) — 19 inspeções, nenhum segredo
```
