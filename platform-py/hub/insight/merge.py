"""Telemetria com várias réplicas no mesmo volume: cada uma soma na memória e, ao gravar, junta com o arquivo.

`merge3(arquivo, meu, base)`: o arquivo traz o que as outras réplicas gravaram; `meu - base` é o que esta réplica
contou desde a última gravação (base = estado adotado na última junção). Nada se perde e todas convergem a cada
gravação. Regras: dict por chave; set = união; número = arquivo + (meu - base); lista de números = posição a posição.
"""

from __future__ import annotations

import copy
from typing import Any


def merge3(file: Any, mine: Any, base: Any) -> Any:
    if isinstance(mine, dict) or isinstance(file, dict):
        f, m, b = (x if isinstance(x, dict) else {} for x in (file, mine, base))
        return {k: merge3(f.get(k), m.get(k), b.get(k)) for k in f.keys() | m.keys()}
    if isinstance(mine, set) or isinstance(file, set):
        return set(file or ()) | set(mine or ())
    if isinstance(mine, list) or isinstance(file, list):
        f, m, b = (list(x) if isinstance(x, list) else [] for x in (file, mine, base))
        n = max(len(f), len(m))
        f, m, b = (x + [0] * (n - len(x)) for x in (f, m, b))
        return [merge3(x, y, z) for x, y, z in zip(f, m, b, strict=True)]
    if isinstance(mine, int | float) or isinstance(file, int | float):
        return (file or 0) + (mine or 0) - (base or 0)
    return mine if mine is not None else file


def snapshot(value: Any) -> Any:
    return copy.deepcopy(value)
