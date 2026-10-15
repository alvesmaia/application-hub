"""Text of container state for the Containers page and the logs: Docker-style durations, uptime, log level."""

from __future__ import annotations


def human_duration(sec: float) -> str:
    """Like `docker ps` (HumanDuration)."""
    s = int(sec)
    if s < 1:
        return "Less than a second"
    if s < 60:
        return f"{s} seconds"
    mi = s // 60
    if mi == 1:
        return "About a minute"
    if mi < 60:
        return f"{mi} minutes"
    return _human_hours(s)


def _human_hours(s: int) -> str:
    h = round(s / 3600)
    if h == 1:
        return "About an hour"
    if h < 48:
        return f"{h} hours"
    d = h // 24
    if d < 14:
        return f"{d} days"
    if d < 60:
        return f"{d // 7} weeks"
    return f"{d // 30} months"


def level_of(kind: str, text: str) -> str:
    t = text.lower()
    return "ERROR" if kind == "stderr" or "error" in t else "WARN" if "warn" in t else "INFO"


def uptime_of(status: str) -> str:
    if not status.startswith("Up "):
        return "—"
    s = status[3:].split(" (")[0]
    for a, b in (("Less than a second", "< 1 s"), ("About a minute", "~1 min"), ("About an hour", "~1 h"),
                 (" seconds", " s"), (" second", " s"), (" minutes", " min"), (" minute", " min"),
                 (" hours", " h"), (" hour", " h"), (" days", " dias"), (" day", " dia"), (" weeks", " semanas"),
                 (" week", " semana"), (" months", " meses"), (" month", " mês")):  # fmt: skip
        s = s.replace(a, b)
    return s
