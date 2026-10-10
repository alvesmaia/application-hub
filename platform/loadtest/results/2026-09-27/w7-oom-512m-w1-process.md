# Onda 7 — memória/OOM do contratos (Python) — CT_MEM=512m, UVICORN_WORKERS=1, HUB_JOBS_MODE=process

`node loadtest/e2e/w7-contratos-oom.mjs --mem 512m --workers 1 --jobs process --imports 2 --rows 250000` (2026-09-27T21:27:44.399Z).
2 importações simultâneas de 250.000 linhas (13.5 MB cada; 247.500 válidas),
leitura com python-calamine (uma planilha carregada por processo por vez: `CONTRATOS_IMPORT_PARALLEL=1`).

| Medida | Valor |
|---|---|
| Limite do container | 512 MiB · 1 processo(s) uvicorn |
| Pico de memória (docker stats, 208 amostras) | **288 MiB** |
| Eventos OOM / die / reinícios | 0 / 0 / 0 |
| Tarefas | `job-df83c4db02146141e2ca056bc79e7d65` done em 1 tentativa(s)<br>`job-fd493c895eefb298d51c30c03a9b1cb8` done em 1 tentativa(s) |
| Contratos gravados por planilha | 247500 (247500 distintos) · 247500 (247500 distintos) — esperado 247500 |
| Todas concluídas | ✅ |
| Efeito exatamente uma vez (sem duplicados) | ✅ |

## Linha do tempo

- 0.0 s — recriando hub-app-contratos com CT_MEM=512m CT_WORKERS=1 HUB_JOBS_MODE=process
- 65.0 s — 2 planilha(s) de 250000 linhas (13.5 MB; 247500 válidas cada)
- 65.6 s — importações enfileiradas: job-fd493c895eefb298d51c30c03a9b1cb8, job-df83c4db02146141e2ca056bc79e7d65
- 118.6 s — fim: job-df83c4db done (1 tentativas) · job-fd493c89 done (1 tentativas) · pico 288 MiB de 512 MiB · OOM 0 · die 0
