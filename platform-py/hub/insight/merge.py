"""Telemetry with several replicas on the same volume: each one adds in memory and, when writing, merges with the file.

`merge3(file, mine, base)`: the file carries what the other replicas wrote; `mine - base` is what this replica
counted since the last write (base = state adopted at the last merge). Nothing is lost and all converge on every
write. Rules: dict per key; set = union; number = file + (mine - base); list of numbers = position by position.
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
