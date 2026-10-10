# Onda 7 — memória/OOM do contratos (Python) — CT_MEM=512m, UVICORN_WORKERS=2, HUB_JOBS_MODE=inline

`node loadtest/e2e/w7-contratos-oom.mjs --mem 512m --workers 2 --jobs inline --imports 2 --rows 250000` (2026-09-27T21:30:13.033Z).
2 importações simultâneas de 250.000 linhas (13.5 MB cada; 247.500 válidas),
leitura com python-calamine (uma planilha carregada por processo por vez: `CONTRATOS_IMPORT_PARALLEL=1`).

| Medida | Valor |
|---|---|
| Limite do container | 512 MiB · 2 processo(s) uvicorn |
| Pico de memória (docker stats, 298 amostras) | **431 MiB** |
| Eventos OOM / die / reinícios | 2 / 0 / 0 |
| Tarefas | `job-192a4fd49de0103cf181f921b3483727` done em 3 tentativa(s)<br>`job-5f4fd56a00e1b7d6d63b0342fe45c31b` done em 2 tentativa(s) |
| Contratos gravados por planilha | 247500 (247500 distintos) · 247500 (247500 distintos) — esperado 247500 |
| Todas concluídas | ✅ |
| Efeito exatamente uma vez (sem duplicados) | ✅ |

## Linha do tempo

- 0.0 s — recriando hub-app-contratos com CT_MEM=512m CT_WORKERS=2 HUB_JOBS_MODE=inline
- 65.3 s — 2 planilha(s) de 250000 linhas (13.5 MB; 247500 válidas cada)
- 66.0 s — importações enfileiradas: job-192a4fd49de0103cf181f921b3483727, job-5f4fd56a00e1b7d6d63b0342fe45c31b
- 68.7 s — engine: oom
- 85.3 s — engine: oom
- 141.4 s — fim: job-192a4fd4 done (3 tentativas) · job-5f4fd56a done (2 tentativas) · pico 431 MiB de 512 MiB · OOM 2 · die 0
