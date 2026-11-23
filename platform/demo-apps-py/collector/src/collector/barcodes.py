"""Barcodes and 2D codes as **SVG generated on the server** (no JavaScript; the app does not run
script próprio — CONTRATO-APP §13).

- 1D: ``python-barcode`` (MIT) only to encode (``build()`` → modules 0/1); the drawing is ours:
  a single ``<path>`` with the bars (integer scale, 10-module quiet zone),
  readable by a camera also on a screen.
- QR: ``segno`` (BSD) → module matrix; DataMatrix: ``ppf-datamatrix`` (MIT) → matrix.

The SVG comes out with ``viewBox`` + ``role="img"``/``aria-label`` and only uses elements accepted by
the hub's sanitizer (``svg``, ``rect``, ``path``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import barcode
import segno
from markupsafe import Markup, escape
from ppf import datamatrix

DataMatrix = vars(datamatrix)["DataMatrix"]

#: Format → (python-barcode class, options). Names follow those of BarcodeDetector.
LINEAR: dict[str, tuple[str, dict[str, Any]]] = {
    "ean_13": ("ean13", {}),
    "ean_8": ("ean8", {}),
    "upc_a": ("upca", {}),
    "code_128": ("code128", {}),
    "code_39": ("code39", {"add_checksum": False}),
}
MATRIX = ("qr_code", "data_matrix")
FORMATS = (*LINEAR, *MATRIX)
LABELS = {
    "ean_13": "EAN-13",
    "ean_8": "EAN-8",
    "upc_a": "UPC-A",
    "code_128": "Code 128",
    "code_39": "Code 39",
    "qr_code": "QR Code",
    "data_matrix": "DataMatrix",
}


@dataclass(frozen=True)
class Encoded:
    fmt: str
    text: str  # full encoded text (with check digit, if any)
    modules: tuple[str, ...]  # module rows "0101…" (1D: one row)


def encode(fmt: str, value: str) -> Encoded:
    if fmt in LINEAR:
        cls, opts = LINEAR[fmt]
        b = barcode.get_barcode_class(cls)(value, **opts)
        rows = b.build()
        return Encoded(fmt, str(b.get_fullcode()), (str(rows[0]),))
    if fmt == "qr_code":
        q = segno.make(value, error="m", micro=False)
        return Encoded(fmt, value, tuple("".join("1" if m else "0" for m in row) for row in q.matrix))
    if fmt == "data_matrix":
        d = DataMatrix(value)
        return Encoded(fmt, value, tuple("".join("1" if m else "0" for m in row) for row in d.matrix))
    raise ValueError(f"formato não suportado: {fmt}")


def _runs(row: str) -> list[tuple[int, int]]:
    """(start, length) of each run of 1s."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(row):
        if row[i] == "1":
            j = i
            while j < len(row) and row[j] == "1":
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def _path_1d(row: str, quiet: int, height: int) -> str:
    return "".join(f"M{x + quiet} 0h{w}v{height}h-{w}z" for x, w in _runs(row))


def _path_2d(rows: Sequence[str], quiet: int) -> str:
    parts: list[str] = []
    for y, row in enumerate(rows):
        for x, w in _runs(row):
            parts.append(f"M{x + quiet} {y + quiet}h{w}v1h-{w}z")
    return "".join(parts)


def svg(fmt: str, value: str, *, label: str = "", module_px: int = 3, height_modules: int = 40) -> Markup:
    """SVG of the code (white background, black bars — the contrast cameras expect, in
    both themes). ``module_px`` = width of one module in px at natural size; CSS can
    ampliar (``viewBox``)."""
    e = encode(fmt, value)
    name = escape(label or f"{LABELS.get(fmt, fmt)} {e.text}")
    if fmt in LINEAR:
        q = 10
        w = len(e.modules[0]) + 2 * q
        d = _path_1d(e.modules[0], q, height_modules)
        vb = f"0 0 {w} {height_modules}"
        pw, ph = w * module_px, height_modules * module_px
    else:
        q = 4 if fmt == "qr_code" else 2
        n = len(e.modules)
        w = len(e.modules[0]) + 2 * q
        d = _path_2d(e.modules, q)
        vb = f"0 0 {w} {n + 2 * q}"
        pw, ph = w * module_px * 2, (n + 2 * q) * module_px * 2
    return Markup(  # noqa: S704 — only numbers and the escaped label
        f'<svg class="col-code col-code-{fmt}" xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
        f'width="{pw}" height="{ph}" preserveAspectRatio="xMidYMid meet" shape-rendering="crispEdges" '
        f'role="img" aria-label="{name}" data-code-format="{fmt}" data-code-value="{escape(e.text)}">'
        f'<rect width="100%" height="100%" fill="#ffffff"></rect><path fill="#000000" d="{d}"></path></svg>'
    )


def full_text(fmt: str, value: str) -> str:
    return encode(fmt, value).text
