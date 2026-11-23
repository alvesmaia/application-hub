"""Input fields an automation declares (:class:`FieldSpec`, checked when the registry is built) and
the server-side validation of the parameters sent by the form or the API (:func:`validate`).

Messages are pt-BR keys translated by :func:`hub_sdk.locale.t` in the request's language.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Literal

from hub_sdk.locale import t
from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldType = Literal["text", "number", "select", "checkbox", "textarea", "date"]
Value = str | int | float | bool | None
TRUE = ("1", "on", "true", "sim", "yes")


class FieldSpec(BaseModel):
    """One input field: ``{name, label, type, required, help, options, min, max, pattern, max_lines}``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1)
    type: FieldType
    required: bool = False
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    max_lines: int | None = Field(default=None, ge=1)
    default: str | int | bool | None = None

    @model_validator(mode="after")
    def _coherent(self) -> FieldSpec:
        problems = [p for check in _CHECKS if (p := check(self))]
        if problems:
            raise ValueError(f"field {self.name!r}: {'; '.join(problems)}")
        return self


def _options_match_type(f: FieldSpec) -> str:
    if f.type == "select" and not f.options:
        return "a select needs options"
    return "options only apply to select" if f.options and f.type != "select" else ""


def _range_is_ordered(f: FieldSpec) -> str:
    if f.min is not None and f.max is not None and f.min > f.max:
        return f"min {f.min} is greater than max {f.max}"
    return ""


def _pattern_compiles(f: FieldSpec) -> str:
    if f.pattern is None:
        return ""
    try:
        re.compile(f.pattern)
    except re.error as e:
        return f"invalid pattern {f.pattern!r}: {e}"
    return ""


def _lines_only_on_textarea(f: FieldSpec) -> str:
    return "max_lines only applies to textarea" if f.max_lines is not None and f.type != "textarea" else ""


_CHECKS: tuple[Callable[[FieldSpec], str], ...] = (
    _options_match_type,
    _range_is_ordered,
    _pattern_compiles,
    _lines_only_on_textarea,
)


def validate(fields: Sequence[FieldSpec], raw: Mapping[str, Any]) -> tuple[dict[str, Value], dict[str, str]]:
    """Coerced values and the errors per field (``{name: message}``); a missing key takes the default."""
    values: dict[str, Value] = {}
    errors: dict[str, str] = {}
    for f in fields:
        try:
            values[f.name] = coerce(f, raw.get(f.name, f.default))
        except ValueError as e:
            errors[f.name] = str(e)
    return values, errors


def coerce(f: FieldSpec, v: Any) -> Value:
    if f.type == "checkbox":
        return v is True or str(v).strip().lower() in TRUE
    s = "" if v is None else str(v).strip()
    if not s:
        if f.required:
            raise ValueError(t("Campo obrigatório."))
        return None
    return _PARSERS[f.type](f, s)


def _text(f: FieldSpec, s: str) -> Value:
    if f.pattern is not None and not re.fullmatch(f.pattern, s):
        raise ValueError(t("Formato inválido."))
    return s


def _number(f: FieldSpec, s: str) -> Value:
    try:
        n = float(s.replace(",", "."))
    except ValueError:
        raise ValueError(t("Informe um número.")) from None
    if (f.min is not None and n < f.min) or (f.max is not None and n > f.max):
        raise ValueError(t("Informe um valor entre {a} e {b}.", a=_num(f.min), b=_num(f.max)))
    return int(n) if n.is_integer() else n


def _num(v: float | None) -> str:
    return "—" if v is None else f"{v:g}"


def _select(f: FieldSpec, s: str) -> Value:
    if s not in {v for v, _ in f.options}:
        raise ValueError(t("Escolha uma das opções."))
    return s


def _textarea(f: FieldSpec, s: str) -> Value:
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if f.max_lines is not None and len(lines) > f.max_lines:
        raise ValueError(t("No máximo {n} linhas preenchidas.", n=f.max_lines))
    return s


def _date(f: FieldSpec, s: str) -> Value:
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError(t("Informe uma data válida.")) from None


_PARSERS: dict[str, Callable[[FieldSpec, str], Value]] = {
    "text": _text,
    "number": _number,
    "select": _select,
    "textarea": _textarea,
    "date": _date,
}
