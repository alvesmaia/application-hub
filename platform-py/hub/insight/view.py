"""Pure view helpers of the insight pages: durations, identifiers shown as code and SVG line charts."""

from __future__ import annotations

from typing import Any

from hub.insight.fmt import num


def ms_text(us: int) -> str:
    m = us / 1000
    return f"{num(m / 1000, 2)} s" if m >= 1000 else f"{num(m, 2 if m < 10 else 0)} ms"


def split_ids(s: str) -> list[tuple[str, bool]]:
    """Separates technical identifiers (`hubt_…`, `HUB_*`) from the text, to show them as code."""
    out: list[tuple[str, bool]] = []
    rest = s
    while True:
        found = [i for i in (rest.find("hubt_"),) if i >= 0]
        for i in range(len(rest) - 4):
            nxt = rest[i + 4 : i + 5]
            if (
                rest.startswith("HUB_", i)
                and nxt.isascii()
                and nxt.isupper()
                and not (i and ((rest[i - 1].isascii() and rest[i - 1].isalnum()) or rest[i - 1] == "_"))
            ):
                found.append(i)
                break
        if not found:
            break
        i = min(found)
        if i:
            out.append((rest[:i], False))
        tail = rest[i:]
        end = next(
            (j for j, c in enumerate(tail) if not ((c.isascii() and c.isalnum()) or c in "_…")), len(tail)
        )
        out.append((tail[:end], True))
        rest = tail[end:]
    if rest:
        out.append((rest, False))
    return out


def chart(title: str, unit: str, series: list[tuple[str, str, list[float | None]]]) -> dict[str, Any]:
    mx = max((v for s in series for v in s[2] if v is not None), default=0.0)
    mx = max(mx, 0.0)
    top = 1.0 if mx <= 0 else mx * 1.15
    n = max(len(series[0][2]) if series else 0, 2)
    lines = [
        (
            " ".join(
                f"{i / (n - 1) * 600:.1f},{140 - (v or 0.0) / top * 140:.1f}" for i, v in enumerate(pts)
            ),
            f"stroke:{color}",
            label,
        )
        for label, color, pts in series
    ]
    return {
        "title": title,
        "subtitle": f"máx. {num(mx, 2 if mx < 10 else 0)} {unit}",
        "lines": lines,
        "ymax": num(top, 2 if top < 10 else 0),
        "ymid": num(top / 2, 2 if top < 20 else 0),
        "empty": mx <= 0,
    }
