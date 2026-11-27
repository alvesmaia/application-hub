"""CPF/CNPJ check digits (offline). Punctuation is ignored; the CNPJ also accepts the alphanumeric
format (letters in the first 12 positions, each character worth ``ord(c) - 48``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CNPJ_W1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
CNPJ_W2 = (6, *CNPJ_W1)


@dataclass(frozen=True)
class Checked:
    document: str
    kind: str | None  # "cpf" | "cnpj" | None (shape of neither)
    valid: bool


def clean(doc: str) -> str:
    return "".join(c for c in doc.upper() if c.isascii() and c.isalnum())


def cpf_ok(d: str) -> bool:
    if len(d) != 11 or not d.isdigit() or len(set(d)) == 1:
        return False
    for n in (9, 10):
        total = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if total * 10 % 11 % 10 != int(d[n]):
            return False
    return True


def _cnpj_digit(body: str, weights: tuple[int, ...]) -> int:
    rest = sum((ord(c) - 48) * w for c, w in zip(body, weights, strict=True)) % 11
    return 0 if rest < 2 else 11 - rest


def cnpj_ok(d: str) -> bool:
    if len(d) != 14 or not d[12:].isdigit() or len(set(d)) == 1:
        return False
    first = _cnpj_digit(d[:12], CNPJ_W1)
    return first == int(d[12]) and _cnpj_digit(d[:13], CNPJ_W2) == int(d[13])


def kind_of(d: str, forced: str) -> str | None:
    if forced in ("cpf", "cnpj"):
        return forced
    if len(d) == 11 and d.isdigit():
        return "cpf"
    return "cnpj" if len(d) == 14 else None


def check(doc: str, forced: str = "auto") -> Checked:
    d = clean(doc)
    kind = kind_of(d, forced)
    valid = cpf_ok(d) if kind == "cpf" else cnpj_ok(d) if kind == "cnpj" else False
    return Checked(doc.strip(), kind, valid)


def validate_lines(text: str, forced: str = "auto") -> dict[str, Any]:
    """Totals and one result per filled line (line numbers as typed)."""
    results = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        c = check(line, forced)
        results.append(
            {"linha": n, "documento": c.document, "tipo": (c.kind or "-").upper(), "valido": c.valid}
        )
    ok = sum(1 for r in results if r["valido"])
    return {"total": len(results), "validos": ok, "invalidos": len(results) - ok, "resultados": results}
