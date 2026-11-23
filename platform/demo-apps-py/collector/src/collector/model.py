"""Schema, initial load and pure rules of the Coletor.

Catalog (~200 items, EAN-13 with prefix 789 + Code128 ``COL-nnnn``), stock locations
(``LOC-A01``…) and 3 receiving orders (``REC-1001``…): used only to name what was
read (the Coletor is a reading bench: Scan, Test sheet, Diagnostics). Everything is
deterministic: the same codes on the screen, in the test sheet and in the tests. Classification
of a reading in :func:`guess_format` and :func:`is_url`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS col_items (
  code text PRIMARY KEY,
  code128 text NOT NULL UNIQUE,
  sku text NOT NULL,
  name text NOT NULL,
  unit text NOT NULL,
  category text NOT NULL
);
CREATE INDEX IF NOT EXISTS col_items_name ON col_items (lower(name));
CREATE TABLE IF NOT EXISTS col_locations (
  code text PRIMARY KEY,
  name text NOT NULL,
  zone text NOT NULL
);
CREATE TABLE IF NOT EXISTS col_orders (
  code text PRIMARY KEY,
  supplier text NOT NULL,
  offset_days int NOT NULL,
  expected_date date NOT NULL,
  status text NOT NULL DEFAULT 'Aguardando'
);
CREATE TABLE IF NOT EXISTS col_order_lines (
  order_code text NOT NULL REFERENCES col_orders(code),
  item text NOT NULL REFERENCES col_items(code),
  expected int NOT NULL,
  received int NOT NULL DEFAULT 0,
  PRIMARY KEY (order_code, item)
);
CREATE TABLE IF NOT EXISTS col_reads (
  id bigserial PRIMARY KEY,
  user_name text NOT NULL,
  value text NOT NULL,
  format text NOT NULL,
  source text NOT NULL,
  kind text NOT NULL,
  label text NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS col_reads_user ON col_reads (user_name, id DESC);
CREATE TABLE IF NOT EXISTS col_prefs (
  user_name text PRIMARY KEY,
  scan_mode text NOT NULL DEFAULT 'barras'
);
ALTER TABLE col_prefs ADD COLUMN IF NOT EXISTS reader_mode text NOT NULL DEFAULT 'interativo';
ALTER TABLE col_prefs ADD COLUMN IF NOT EXISTS cooldown_ms int NOT NULL DEFAULT 2000;
ALTER TABLE col_prefs ADD COLUMN IF NOT EXISTS hold boolean NOT NULL DEFAULT false;
ALTER TABLE col_prefs ADD COLUMN IF NOT EXISTS last_value text NOT NULL DEFAULT '';
ALTER TABLE col_prefs ADD COLUMN IF NOT EXISTS last_at double precision NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS col_sessions (
  user_name text PRIMARY KEY,
  state jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
"""

# Name sets (deterministic) for the demo catalog.
FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Fixação",
        "un",
        (
            "Parafuso sextavado M{n} x 40",
            "Porca autotravante M{n}",
            "Arruela lisa {n} mm",
            "Bucha de nylon {n} mm",
        ),
    ),
    (
        "EPI",
        "un",
        (
            "Luva nitrílica tam. {s}",
            "Óculos de proteção incolor",
            "Protetor auricular plug",
            "Capacete classe B {c}",
        ),
    ),
    (
        "Elétrica",
        "un",
        (
            "Cabo flexível {n} mm² (rolo)",
            "Disjuntor DIN {n} A",
            "Tomada 2P+T {n} A",
            "Fita isolante 19 mm {c}",
        ),
    ),
    (
        "Embalagem",
        "cx",
        (
            "Caixa de papelão nº {n}",
            "Fita adesiva transparente 48 mm",
            "Filme stretch {n} µm",
            "Etiqueta térmica 100 x {n}",
        ),
    ),
    ("Limpeza", "un", ("Pano multiuso {c}", "Detergente neutro 5 L", "Álcool 70% 1 L", "Saco de lixo {n} L")),
    (
        "Escritório",
        "un",
        ("Caneta esferográfica {c}", "Bloco adesivo {n} folhas", "Grampo 26/6 cx", "Papel A4 75 g (resma)"),
    ),
)
SIZES = ("P", "M", "G", "GG")
COLORS = ("azul", "preto", "vermelho", "verde", "amarelo", "branco")
ZONES = (("A", "Corredor A"), ("B", "Corredor B"), ("C", "Corredor C"))
SUPPLIERS = ("Distribuidora Paraná Ltda", "Atacado Minas Industrial", "Sul Embalagens S.A.")
N_ITEMS = 200


def ean13_check(body12: str) -> str:
    """EAN-13 check digit (alternating weights 1, 3)."""
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return str((10 - s % 10) % 10)


def valid_ean13(code: str) -> bool:
    return len(code) == 13 and code.isdigit() and ean13_check(code[:12]) == code[12]


@dataclass(frozen=True)
class Item:
    code: str
    code128: str
    sku: str
    name: str
    unit: str
    category: str


@dataclass(frozen=True)
class Location:
    code: str
    name: str
    zone: str


@dataclass(frozen=True)
class Order:
    code: str
    supplier: str
    offset_days: int
    lines: tuple[tuple[str, int], ...]


def catalog() -> list[Item]:
    rnd = random.Random(424242)  # noqa: S311 — demo data, not a secret
    out: list[Item] = []
    for i in range(1, N_ITEMS + 1):
        cat, unit, names = FAMILIES[(i - 1) % len(FAMILIES)]
        tpl = names[((i - 1) // len(FAMILIES)) % len(names)]
        name = tpl.format(
            n=rnd.choice((4, 6, 8, 10, 12, 16, 20, 25, 32)), s=rnd.choice(SIZES), c=rnd.choice(COLORS)
        )
        body = f"7890000{i:05d}"
        code = body + ean13_check(body)
        out.append(
            Item(code=code, code128=f"COL-{i:04d}", sku=f"SKU-{i:04d}", name=name, unit=unit, category=cat)
        )
    return out


def locations() -> list[Location]:
    return [
        Location(code=f"LOC-{z}{n:02d}", name=f"{label} · Prateleira {n:02d}", zone=label)
        for z, label in ZONES
        for n in range(1, 5)
    ]


def orders() -> list[Order]:
    items = catalog()
    return [
        Order(
            "REC-1001",
            SUPPLIERS[0],
            0,
            tuple((items[i].code, q) for i, q in ((0, 12), (1, 6), (2, 24), (6, 10), (7, 4))),
        ),
        Order(
            "REC-1002",
            SUPPLIERS[1],
            1,
            tuple((items[i].code, q) for i, q in ((12, 3), (13, 8), (18, 20), (19, 2), (24, 5), (25, 6))),
        ),
        Order(
            "REC-1003",
            SUPPLIERS[2],
            -1,
            tuple(
                (items[i].code, q)
                for i, q in ((30, 50), (31, 10), (36, 1), (40, 7), (41, 9), (42, 12), (48, 3))
            ),
        ),
    ]


def normalize_code(raw: str) -> str:
    """Read code (camera, reader or keyboard): no spaces/control chars; UPC-A (12 digits)
    becomes EAN-13 with a leading zero; uppercase letters (Code128/Code39 of the catalog)."""
    c = "".join(ch for ch in str(raw) if ch.isprintable() and not ch.isspace())[:64]
    if len(c) == 12 and c.isdigit():
        c = "0" + c
    return c.upper()


def line_status(expected: int, received: int) -> str:
    if received == 0:
        return "Pendente"
    if received < expected:
        return "Parcial"
    if received == expected:
        return "Completo"
    return "Excedente"


STATUS_CLASS = {
    "Pendente": "col-st-pending",
    "Parcial": "col-st-partial",
    "Completo": "col-st-ok",
    "Excedente": "col-st-over",
}


def is_url(raw: str) -> bool:
    """Is the read value a web address (http/https) and not a code?"""
    v = raw.strip().lower()
    return v.startswith(("http://", "https://")) or "://" in v[:12]


def guess_format(raw: str) -> str:
    """Likely format of a reading with no format given (physical reader/keyboard)."""
    v = raw.strip()
    if is_url(v):
        return "qr_code"
    if v.isdigit():
        if len(v) == 13 and valid_ean13(v):
            return "ean_13"
        if len(v) == 8:
            return "ean_8"
        if len(v) == 12:
            return "upc_a"
        if len(v) == 14:
            return "itf"
    return "code_128"
