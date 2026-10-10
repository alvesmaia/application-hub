# DR — recuperação de desastre (2026-09-28T14:10:04.443Z)

Compose no ar com o seed (`ativos`: banco `ativos` com 1390098 ativos; `fornecedores`: bucket `fornecedores`).
Backups **agendados** (cron `* * * * *`, fuso America/Sao_Paulo) por containers de tarefa
(`pg_dump -Fc -Z 6`, `mc mirror`) na rede `hub-data`; restauração pela API (`POST /api/ops/backups/records/{id}/restore`, o botão "Restaurar").

## Números

| | ativos (banco + arquivos) | fornecedores (bucket + arquivos) |
|---|---|---|
| Backup agendado | banco ativos (83,9 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 83.9 MiB · 10.3 s | bucket fornecedores (8 objetos) · 3 arquivos · crash-consistent · 192 KiB · 0.8 s |
| Restauração (job: verificar → manutenção → parar → snapshot de segurança → banco de lado + pg_restore → mc → arquivos → religar + health) | 39.7 s | 3.6 s |

- **RTO medido** (do desastre até os dois apps respondendo, restaurações em paralelo): **44.6 s** (inclui o tempo de detecção deste script; a restauração em si: 39.7 s).
- **RPO**: igual ao intervalo da agenda — com `* * * * *` perde-se no máximo ~1 min (+ jitter configurado + duração do backup); com a política padrão `Diário 02:00` o RPO é de até 24 h. Execução perdida com o hub fora do ar: roda uma vez ao voltar (política `executar`).

## Integridade

| Verificação | Resultado |
|---|---|
| linhas + md5 da tabela assets | ✅ 1390098 linhas, md5 2d2d72ea53d3e0033a3a13a86af17235 |
| tabelas do hub-sdk (hub_jobs) presentes | ✅ 1 |
| anexos byte a byte (sha256 pelo app) | ✅ 5 anexos |
| arquivos dos apps (sha256) | ✅ 6 arquivos |
| apps respondendo (ativos/assets e ficha do fornecedor) | ✅ HTTP 200 |
| "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps | ✅ {"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409}} |
| snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) | ✅ fornecedores |
| manutenção desligada no fim | ✅ desligada |
| pacotes cifrados em repouso (partes .hubenc, nenhuma parte em claro no volume, manifesto com a cifra) | ✅ 25 partes cifradas em 7 pacotes; 0 partes em claro |
| credenciais fora do env dos containers de tarefa (docker inspect) | ✅ 52 inspeções, nenhum segredo |

## Linha do tempo

```
0.1 s — login ok
6.6 s — referência: assets 1390098 linhas · 5 anexos · 6 arquivos de dados
6.7 s — políticas: a cada 1 min (cron "* * * * *", sem jitter)
51.9 s — agendado fornecedores: bucket fornecedores (8 objetos) · 3 arquivos · crash-consistent · 0.2 MiB em 0.8 s
60.9 s — agendado ativos: banco ativos (83,9 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 83.9 MiB em 10.3 s
60.9 s — agendas de volta para Manual (nada novo é copiado depois do desastre)
63.1 s — DESASTRE: DROP DATABASE ativos, bucket fornecedores vazio (0 anexos visíveis), dados dos apps apagados; ativos/assets → HTTP 200
65.7 s — ativos durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
65.8 s — fornecedores durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
66.7 s — restaurado fornecedores em 3.6 s: Restauração de Rede de Fornecedores concluída — snapshot de segurança fornecedores-1790604555358 (0 B) · bucket fornecedores (8 objetos) · 3 arquivos · hub-app-fornecedores religado e saudável em 0.0 s.
102.8 s — restaurado ativos em 39.7 s: Restauração de Gestão de Ativos concluída — snapshot de segurança: estado atual vazio (nada a guardar) · banco ativos restaurado · 3 arquivos · hub-app-ativos religado e saudável em 0.0 s.
102.8 s — snapshots de segurança registrados: fornecedores (snapshot de segurança antes de restaurar · bucket fornecedores (0 objetos))
104.9 s — manutenção depois: nenhuma · env dos containers de tarefa inspecionados: 52 · credenciais no env: não
112.7 s — OK  linhas + md5 da tabela assets — 1390098 linhas, md5 2d2d72ea53d3e0033a3a13a86af17235
112.7 s — OK  tabelas do hub-sdk (hub_jobs) presentes — 1
112.7 s — OK  anexos byte a byte (sha256 pelo app) — 5 anexos
112.7 s — OK  arquivos dos apps (sha256) — 6 arquivos
112.7 s — OK  apps respondendo (ativos/assets e ficha do fornecedor) — HTTP 200
112.7 s — OK  "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps — {"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409}}
112.7 s — OK  snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) — fornecedores
112.7 s — OK  manutenção desligada no fim — desligada
112.7 s — OK  pacotes cifrados em repouso (partes .hubenc, nenhuma parte em claro no volume, manifesto com a cifra) — 25 partes cifradas em 7 pacotes; 0 partes em claro
112.7 s — OK  credenciais fora do env dos containers de tarefa (docker inspect) — 52 inspeções, nenhum segredo
```
