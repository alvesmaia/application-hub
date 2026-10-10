# DR — recuperação de desastre (2026-09-28T03:38:46.128Z)

Compose no ar com o seed (`ativos`: banco `ativos` com 701071 ativos; `fornecedores`: bucket `fornecedores`).
Backups **agendados** (cron `* * * * *`, fuso America/Sao_Paulo) por containers de tarefa
(`pg_dump -Fc -Z 6`, `mc mirror`) na rede `hub-data`; restauração pela API (`POST /api/ops/backups/records/{id}/restore`, o botão "Restaurar").

## Números

| | ativos (banco + arquivos) | fornecedores (bucket + arquivos) |
|---|---|---|
| Backup agendado | banco ativos (41,5 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 41.5 MiB · 5.4 s | bucket fornecedores (55 objetos) · 3 arquivos · crash-consistent · 12890 KiB · 1.1 s |
| Restauração (job: verificar → manutenção → parar → snapshot de segurança → banco de lado + pg_restore → mc → arquivos → religar + health) | 21.2 s | 4.8 s |

- **RTO medido** (do desastre até os dois apps respondendo, restaurações em paralelo): **24.8 s** (inclui o tempo de detecção deste script; a restauração em si: 21.2 s).
- **RPO**: igual ao intervalo da agenda — com `* * * * *` perde-se no máximo ~1 min (+ jitter configurado + duração do backup); com a política padrão `Diário 02:00` o RPO é de até 24 h. Execução perdida com o hub fora do ar: roda uma vez ao voltar (política `executar`).

## Integridade

| Verificação | Resultado |
|---|---|
| linhas + md5 da tabela assets | ✅ 701071 linhas, md5 5730f2b4a0b8c568e4c587ded31c7f50 |
| tabelas do hub-sdk (hub_jobs) presentes | ✅ 1 |
| anexos byte a byte (sha256 pelo app) | ✅ 20 anexos |
| arquivos dos apps (sha256) | ✅ 6 arquivos |
| apps respondendo (ativos/assets e ficha do fornecedor) | ✅ HTTP 200 |
| "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps | ✅ {"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409}} |
| snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) | ✅ fornecedores |
| manutenção desligada no fim | ✅ desligada |
| pacotes cifrados em repouso (partes .hubenc, nenhuma parte em claro no volume, manifesto com a cifra) | ✅ 106 partes cifradas em 4 pacotes; 0 partes em claro |
| credenciais fora do env dos containers de tarefa (docker inspect) | ✅ 29 inspeções, nenhum segredo |

## Linha do tempo

```
0.1 s — login ok
3.8 s — referência: assets 701071 linhas · 20 anexos · 6 arquivos de dados
3.9 s — políticas: a cada 1 min (cron "* * * * *", sem jitter)
43.1 s — agendado fornecedores: bucket fornecedores (55 objetos) · 3 arquivos · crash-consistent · 12.6 MiB em 1.1 s
49.1 s — agendado ativos: banco ativos (41,5 MB) · 3 arquivos · sem hooks no manifesto (crash-consistent) · 41.5 MiB em 5.4 s
49.1 s — agendas de volta para Manual (nada novo é copiado depois do desastre)
51.1 s — DESASTRE: DROP DATABASE ativos, bucket fornecedores vazio (0 anexos visíveis), dados dos apps apagados; ativos/assets → HTTP 200
53.7 s — fornecedores durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
53.9 s — ativos durante a restauração: "Em manutenção (restauração)" · reiniciar → HTTP 409 · backup → HTTP 409
55.9 s — restaurado fornecedores em 4.8 s: Restauração de Rede de Fornecedores concluída — snapshot de segurança fornecedores-1790566699929 (0 B) · bucket fornecedores (55 objetos) · 3 arquivos · hub-app-fornecedores religado e saudável em 0.0 s.
72.3 s — restaurado ativos em 21.2 s: Restauração de Gestão de Ativos concluída — snapshot de segurança: estado atual vazio (nada a guardar) · banco ativos restaurado · 3 arquivos · hub-app-ativos religado e saudável em 0.0 s.
72.3 s — snapshots de segurança registrados: fornecedores (snapshot de segurança antes de restaurar · bucket fornecedores (0 objetos))
73.6 s — manutenção depois: nenhuma · env dos containers de tarefa inspecionados: 29 · credenciais no env: não
78.1 s — OK  linhas + md5 da tabela assets — 701071 linhas, md5 5730f2b4a0b8c568e4c587ded31c7f50
78.1 s — OK  tabelas do hub-sdk (hub_jobs) presentes — 1
78.1 s — OK  anexos byte a byte (sha256 pelo app) — 20 anexos
78.1 s — OK  arquivos dos apps (sha256) — 6 arquivos
78.1 s — OK  apps respondendo (ativos/assets e ficha do fornecedor) — HTTP 200
78.1 s — OK  "Em manutenção (restauração)" visível e bloqueando reiniciar/backup (409) nos dois apps — {"fornecedores":{"label":"Em manutenção (restauração)","restart":409,"backup":409},"ativos":{"label":"Em manutenção (restauração)","restart":409,"backup":409}}
78.1 s — OK  snapshot de segurança antes de restaurar (registro "Pré-restauração"; banco apagado no desastre vira nota) — fornecedores
78.1 s — OK  manutenção desligada no fim — desligada
78.1 s — OK  pacotes cifrados em repouso (partes .hubenc, nenhuma parte em claro no volume, manifesto com a cifra) — 106 partes cifradas em 4 pacotes; 0 partes em claro
78.1 s — OK  credenciais fora do env dos containers de tarefa (docker inspect) — 29 inspeções, nenhum segredo
```
