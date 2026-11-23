"""Schedule rules: every N minutes (N ≥ 5) or daily at HH:MM São Paulo time; the next fire
(:func:`next_run`, pure) and the form/API validation (:class:`ScheduleIn`)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from hub_sdk.locale import t
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator

from automacoes.present import TZ

MIN_EVERY, MAX_EVERY = 5, 1440
HHMM = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
# Form field names (pt-BR, like the rest of the UI) → model fields.
FORM_FIELDS = {"tipo": "kind", "intervalo": "every_minutes", "horario": "at_time"}


class ScheduleIn(BaseModel):
    kind: Literal["intervalo", "diario"]
    every_minutes: int | None = Field(default=None, validate_default=True)
    at_time: str | None = Field(default=None, validate_default=True)

    @field_validator("every_minutes", mode="before")
    @classmethod
    def _every(cls, v: Any, info: ValidationInfo) -> int | None:
        if info.data.get("kind") != "intervalo":
            return None
        try:
            n = int(str(v).strip())
        except ValueError:
            n = 0
        if not MIN_EVERY <= n <= MAX_EVERY:
            raise ValueError(t("Informe de {a} a {b} minutos.", a=MIN_EVERY, b=MAX_EVERY))
        return n

    @field_validator("at_time", mode="before")
    @classmethod
    def _at(cls, v: Any, info: ValidationInfo) -> str | None:
        if info.data.get("kind") != "diario":
            return None
        m = HHMM.fullmatch(str(v or "").strip())
        if m is None:
            raise ValueError(t("Informe o horário no formato HH:MM."))
        return f"{int(m[1]):02d}:{m[2]}"


def parse_schedule(form: Mapping[str, Any]) -> tuple[ScheduleIn | None, dict[str, str]]:
    """Rule from the form (``tipo``, ``intervalo``, ``horario``) or the errors per form field."""
    data = {model: form.get(name) for name, model in FORM_FIELDS.items() if form.get(name) not in (None, "")}
    try:
        return ScheduleIn.model_validate(data), {}
    except ValidationError as e:
        return None, _errors(e)


def _errors(e: ValidationError) -> dict[str, str]:
    by_model = {model: name for name, model in FORM_FIELDS.items()}
    out: dict[str, str] = {}
    for err in e.errors():
        name = by_model.get(str(err["loc"][0]), "tipo") if err["loc"] else "tipo"
        cause = (err.get("ctx") or {}).get("error")
        out.setdefault(name, str(cause) if cause is not None else t("Escolha o tipo de agendamento."))
    return out


def next_run(rule: ScheduleIn, now: datetime) -> datetime:
    """Next fire after ``now`` (UTC). Interval: ``now + N min`` (no catch-up storm after a pause);
    daily: the next HH:MM in São Paulo strictly after ``now``."""
    if rule.kind == "intervalo":
        return now + timedelta(minutes=rule.every_minutes or MIN_EVERY)
    hh, mm = (int(x) for x in (rule.at_time or "00:00").split(":"))
    local = now.astimezone(TZ)
    at = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def describe(rule: ScheduleIn) -> str:
    if rule.kind == "intervalo":
        return t("a cada {n} min", n=rule.every_minutes)
    return t("diário às {h}", h=rule.at_time)
