"""Daily availability kept for a year: the detailed buckets (`metrics.json`) last 30 days, the Status page shows 12
months. Each save folds the buckets into one row per day `[probes, probes_ok, requests, errors]`."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol, cast

from hub.filelock import file_lock

DAY = 86_400
KEEP_DAYS = 400
Days = dict[str, dict[str, list[int]]]  # series "app|env" -> day start (epoch s, as text) -> row


class _Bucket(Protocol):
    t: int
    probes: int
    probes_ok: int
    count: int
    errors: int


def read(path: Path) -> Days:
    try:
        return cast(Days, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def fold(buckets: Iterable[_Bucket]) -> dict[str, list[int]]:
    days: dict[str, list[int]] = {}
    for b in buckets:
        row = days.setdefault(str(b.t - b.t % DAY), [0, 0, 0, 0])
        for i, v in enumerate((b.probes, b.probes_ok, b.count, b.errors)):
            row[i] += v
    return days


def merge(into: dict[str, list[int]], new: dict[str, list[int]], now: int) -> None:
    """A day leaving the detailed window has fewer samples: it never overwrites the complete row."""
    for d, row in new.items():
        old = into.get(d)
        if old is None or row[0] + row[2] >= old[0] + old[2]:
            into[d] = row
    for d in [d for d in into if int(d) < now - KEEP_DAYS * DAY]:
        del into[d]


def save(path: Path, series: Mapping[str, Iterable[_Bucket]], now: int) -> Days:
    """Merges the current buckets into the file (other replicas' rows kept by the same rule); returns the result."""
    with file_lock(path):
        cur = read(path)
        for k, buckets in series.items():
            merge(cur.setdefault(k, {}), fold(buckets), now)
        tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cur, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    return cur


def uptime(row: list[int] | None) -> float | None:
    """OK probes; without probes, success rate; without anything, `None`."""
    if not row:
        return None
    probes, ok, count, errors = row
    return ok / probes if probes else (1.0 - errors / count if count else None)
