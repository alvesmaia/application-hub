# DR — onda 5, com as correções (2026-09-27T10:48:19.275Z)

Compose no ar com o seed (`ativos`: banco `ativos` com 1788820 ativos; `fornecedores`: bucket `fornecedores`).
Backups **agendados** (cron `* * * * *`, fuso America/Sao_Paulo) por containers de tarefa
(`pg_dump -Fc -Z 6`, `mc mirror`) na rede `hub-data`; restauração pela API (`POST /api/ops/backups/records/{id}/restore`, o botão "Restaurar").

## Números

| | ativos (banco + arquivos) | fornecedores (bucket + arquivos) |
|---|---|---|
| Backup agendado | banco ativos (108,5 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 108.5 MiB · 13.3 s | banco app_fornecedores (8,0 KB) · bucket fornecedores (21 objetos) · 2 arquivos · sem hooks no manifesto (crash-consistent) · 522 KiB · 1.6 s |
| Restauração (job: verificar → manutenção → parar → snapshot de segurança → banco de lado + pg_restore → mc → arquivos → religar + health) | 51.8 s | 8.6 s |

- **RTO medido** (do desastre até os dois apps respondendo, restaurações em paralelo): **57.1 s** (inclui o tempo de detecção deste script; a restauração em si: 51.8 s).
- **RPO**: igual ao intervalo da agenda — com `* * * * *` perde-se no máximo ~1 min (+ jitter configurado + duração do backup); com a política padrão `Diário 02:00` o RPO é de até 24 h. Execução perdida com o hub fora do ar: roda uma vez ao voltar (política `executar`).

## Integridade

| Verificação | Resultado |
|---|---|
| linhas + md5 da tabela assets | ✅ 1788820 linhas, md5 372e2e029a9615d67ce8e68e2e827fd0 |
| tabelas do hub-sdk (hub_jobs) presentes | ✅ 1 |
| anexos byte a byte (sha256 pelo app) | ✅ 21 anexos |
| arquivos dos apps (sha256) | ✅ 5 arquivos |
| apps respondendo (ativos/assets e ficha do fornecedor) | ✅ HTTP 200 |
| "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps | ✅ {"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409}} |
| snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) | ✅ fornecedores |
| manutenção desligada no fim | ✅ desligada |
| credenciais fora do env dos containers de tarefa (docker inspect) | ✅ 85 inspeções, nenhum segredo |

## Linha do tempo

```
0.1 s — login ok
8.4 s — referência: assets 1788820 linhas · 21 anexos · 5 arquivos de dados
8.4 s — políticas: a cada 1 min (cron "* * * * *", sem jitter)
29.5 s — agendado fornecedores: banco app_fornecedores (8,0 KB) · bucket fornecedores (21 objetos) · 2 arquivos · sem hooks no manifesto (crash-consistent) · 0.5 MiB em 1.6 s
41.5 s — agendado ativos: banco ativos (108,5 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 108.5 MiB em 13.3 s
41.6 s — agendas de volta para Manual (nada novo é copiado depois do desastre)
43.7 s — DESASTRE: DROP DATABASE ativos, bucket fornecedores vazio (0 anexos visíveis), dados dos apps apagados; ativos/assets → HTTP 200
45.3 s — ativos durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
46.3 s — fornecedores durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
52.3 s — restaurado fornecedores em 8.6 s: Restauração de Rede de Fornecedores concluída — snapshot de segurança fornecedores-1790506036831 (8,0 KB) · banco app_fornecedores restaurado · bucket fornecedores (21 objetos) · 2 arquivos · hub-app-fornecedores religado e saudável em 0.0 s.
95.5 s — restaurado ativos em 51.8 s: Restauração de Gestão de Ativos concluída — snapshot de segurança: estado atual vazio (nada a guardar) · banco ativos restaurado · 3 arquivos · hub-app-ativos religado e saudável em 0.0 s.
95.5 s — snapshots de segurança registrados: fornecedores (snapshot de segurança antes de restaurar · banco app_fornecedores (8,0 KB) · bucket fornecedores (0 objetos))
97.6 s — manutenção depois: nenhuma · env dos containers de tarefa inspecionados: 85 · credenciais no env: não
106.7 s — OK  linhas + md5 da tabela assets — 1788820 linhas, md5 372e2e029a9615d67ce8e68e2e827fd0
106.7 s — OK  tabelas do hub-sdk (hub_jobs) presentes — 1
106.7 s — OK  anexos byte a byte (sha256 pelo app) — 21 anexos
106.7 s — OK  arquivos dos apps (sha256) — 5 arquivos
106.7 s — OK  apps respondendo (ativos/assets e ficha do fornecedor) — HTTP 200
106.7 s — OK  "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps — {"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409}}
106.7 s — OK  snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) — fornecedores
106.7 s — OK  manutenção desligada no fim — desligada
106.7 s — OK  credenciais fora do env dos containers de tarefa (docker inspect) — 85 inspeções, nenhum segredo
```
