"""Friendly display of parameters and outputs (label/value rows, lists of objects as small
tables), timestamps in São Paulo time and durations — in the request's language."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hub_sdk import locale
from hub_sdk.locale import t

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _zone() -> tzinfo:
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        # ponytail: no tz database (e.g. Windows without tzdata) → fixed UTC-3, exact since
        # São Paulo dropped DST in 2019; install tzdata if the rule ever changes again.
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


TZ = _zone()


@dataclass(frozen=True)
class Row:
    """``label: text``, or a small table (``columns`` + ``cells``) for a list of objects."""

    label: str
    text: str = ""
    columns: tuple[str, ...] = ()
    cells: tuple[tuple[str, ...], ...] = ()


def humanize(key: str) -> str:
    """``snake_case`` → ``Snake case``."""
    s = key.replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def display(v: Any) -> str:
    if isinstance(v, bool):
        return t("Sim") if v else t("Não")
    if v is None or v == "":
        return "—"
    if isinstance(v, date):
        return locale.date(v)
    if isinstance(v, float):
        return locale.number(v, 2).rstrip("0").rstrip(",.")
    if isinstance(v, str) and ISO_DATE.fullmatch(v):
        return locale.date(date.fromisoformat(v))
    if isinstance(v, list):
        return ", ".join(display(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{humanize(str(k))}: {display(x)}" for k, x in v.items())
    return str(v)


def rows(values: Mapping[str, Any], label_of: Callable[[str], str]) -> list[Row]:
    return [_row(t(label_of(k)), v, label_of) for k, v in values.items()]


def _row(label: str, v: Any, label_of: Callable[[str], str]) -> Row:
    if not (isinstance(v, list) and v and all(isinstance(x, dict) for x in v)):
        return Row(label, display(v))
    keys = list(dict.fromkeys(k for item in v for k in item))
    cells = tuple(tuple(display(item.get(k)) for k in keys) for item in v)
    return Row(label, columns=tuple(t(label_of(k)) for k in keys), cells=cells)


def when(d: datetime | None) -> str:
    """``23/11/2026 08:05`` (pt-BR) or ``11/23/2026 08:05`` (en), São Paulo time."""
    if d is None:
        return "—"
    local = d.astimezone(TZ)
    return f"{locale.date(local.date())} {local:%H:%M}"


def duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60000:
        return f"{locale.number(ms / 1000, 1)} s"
    return f"{ms // 60000} min {ms % 60000 // 1000} s"
