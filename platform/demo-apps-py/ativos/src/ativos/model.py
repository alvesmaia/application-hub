"""Asset registry columns (DB, spreadsheet), validation/normalization and SQL schema.

30 columns, with fixed headers, limits and error messages (spreadsheet and DB use the same).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any

CATEGORIES = (
    "Tecnologia",
    "Mobiliário",
    "Veículos",
    "Audiovisual",
    "Telefonia",
    "Ferramentas",
    "Climatização",
    "Segurança",
)
LOCATIONS = (
    "São Paulo",
    "Rio de Janeiro",
    "Curitiba",
    "Belo Horizonte",
    "Porto Alegre",
    "Recife",
    "Brasília",
    "Remoto",
)
STATUSES = ("Disponível", "Em uso", "Manutenção", "Baixado")
CONDITIONS = ("Novo", "Bom", "Regular", "Ruim")

PAGE_SIZE = 50
MAX_PAGE = 10_000

# Key → (SQL column, label). The order is that of the table headers.
SORTS: tuple[tuple[str, str, str], ...] = (
    ("nome", "name", "Ativo"),
    ("patrimonio", "tag", "Patrimônio"),
    ("categoria", "category", "Categoria"),
    ("localizacao", "location", "Localização"),
    ("status", "status", "Status"),
    ("aquisicao", "acquisition_date", "Aquisição"),
    ("valor", "acquisition_value_cents", "Valor"),
)


@dataclass(frozen=True)
class Col:
    key: str
    header: str
    kind: str  # text | enum | date | money | int
    required: bool
    max_len: int = 200
    options: tuple[str, ...] = ()
    lo: int = 0
    hi: int = 0


def _t(key: str, header: str, n: int, req: bool = False) -> Col:
    return Col(key, header, "text", req, max_len=n)


def _e(key: str, header: str, opts: tuple[str, ...], req: bool = False) -> Col:
    return Col(key, header, "enum", req, options=opts)


def _d(key: str, header: str) -> Col:
    return Col(key, header, "date", False)


def _m(key: str, header: str) -> Col:
    return Col(key, header, "money", False)


def _i(key: str, header: str, lo: int, hi: int) -> Col:
    return Col(key, header, "int", False, lo=lo, hi=hi)


# The 30 business columns, in spreadsheet order.
COLS: tuple[Col, ...] = (
    _t("tag", "Patrimônio", 40, True),
    _t("name", "Nome", 160, True),
    _e("category", "Categoria", CATEGORIES, True),
    _e("location", "Localização", LOCATIONS, True),
    _e("status", "Status", STATUSES, True),
    _t("brand", "Marca", 80),
    _t("model", "Modelo", 120),
    _t("serial_number", "Número de série", 80),
    _t("supplier", "Fornecedor", 120),
    _t("supplier_cnpj", "CNPJ do fornecedor", 18),
    _t("cost_center", "Centro de custo", 40),
    _t("department", "Departamento", 80),
    _t("responsible", "Responsável", 120),
    _t("building", "Prédio", 80),
    _t("room", "Sala", 40),
    _d("acquisition_date", "Data de aquisição"),
    _m("acquisition_value_cents", "Valor de aquisição"),
    _m("residual_value_cents", "Valor residual"),
    _i("useful_life_months", "Vida útil (meses)", 1, 600),
    _d("warranty_until", "Garantia até"),
    _t("invoice_number", "Nota fiscal", 40),
    _e("condition", "Conservação", CONDITIONS),
    _t("hostname", "Hostname", 80),
    _t("os", "Sistema operacional", 80),
    _t("cpu", "Processador", 80),
    _i("ram_gb", "Memória (GB)", 0, 4096),
    _i("storage_gb", "Armazenamento (GB)", 0, 1_000_000),
    _t("ip_address", "Endereço IP", 45),
    _d("last_audit", "Última auditoria"),
    _t("notes", "Observações", 1000),
)
COL = {c.key: c for c in COLS}
# Fields editable in the panel (form and API).
EDIT_FIELDS = (
    "tag",
    "name",
    "category",
    "location",
    "status",
    "responsible",
    "department",
    "acquisition_date",
    "acquisition_value_cents",
    "notes",
)


class Invalid(ValueError):
    pass


def _norm_key(s: str) -> str:
    return s.strip().lower()


def map_headers(headers: list[str]) -> list[int | None]:
    """Maps the spreadsheet headers to indices of :data:`COLS` (error if a required one is missing
    or a header is repeated)."""
    by = {_norm_key(c.header): i for i, c in enumerate(COLS)}
    seen = [False] * len(COLS)
    out: list[int | None] = []
    for h in headers:
        idx = by.get(_norm_key(h))
        if idx is not None:
            if seen[idx]:
                raise Invalid(f"Coluna repetida na planilha: {COLS[idx].header}.")
            seen[idx] = True
        out.append(idx)
    missing = [c.header for c, s in zip(COLS, seen, strict=True) if c.required and not s]
    if missing:
        raise Invalid(f"Colunas obrigatórias ausentes: {', '.join(missing)}.")
    return out


def serial_to_date(serial: float) -> dt.date | None:
    if not 1.0 <= serial <= 2_958_465.0:
        return None
    return dt.date(1899, 12, 30) + dt.timedelta(days=math.floor(serial))


def parse_date(s: str) -> dt.date | None:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        return d if 1950 <= d.year <= 2100 else None
    return None


def _money_from_float(v: float) -> int | None:
    if math.isfinite(v) and 0.0 <= v < 1e12:
        return math.floor(v * 100 + 0.5)
    return None


def parse_money(s: str) -> int | None:
    """ "1.234,56", "1234.56", "R$ 1.234" → centavos."""
    t = "".join(ch for ch in s.strip().removeprefix("R$").strip() if not ch.isspace())
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return _money_from_float(v)


def _number_text(v: float) -> str:
    return str(int(v)) if v == int(v) and abs(v) < 1e15 else str(v)


def _is_num(raw: Any) -> bool:
    return isinstance(raw, int | float) and not isinstance(raw, bool)


_INT = re.compile(r"^[+-]?\d+$")


def normalize(col: Col, raw: Any) -> Any:  # noqa: C901, PLR0912, PLR0915
    """Normalized value (``None`` = empty/NULL) — text, date, cents or integer — or
    :class:`Invalid` with the column's error message."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if col.required:
            raise Invalid("campo obrigatório")
        return None
    k = col.kind
    if k == "text":
        if isinstance(raw, str):
            s = raw.strip()
        elif isinstance(raw, bool):
            s = "Sim" if raw else "Não"
        elif _is_num(raw):
            s = _number_text(float(raw))
        else:
            s = str(raw).strip()
        if len(s) > col.max_len:
            raise Invalid(f"texto acima de {col.max_len} caracteres")
        if any(not c.isprintable() and c != "\n" for c in s):
            raise Invalid("caractere de controle não permitido")
        return s
    if k == "enum":
        s = raw.strip() if isinstance(raw, str) else (_number_text(float(raw)) if _is_num(raw) else "")
        for o in col.options:
            if o.lower() == s.lower():
                return o
        raise Invalid(f"valor inválido (use: {', '.join(col.options)})")
    if k == "date":
        d: dt.date | None = None
        if isinstance(raw, dt.datetime):
            d = raw.date()
        elif isinstance(raw, dt.date):
            d = raw
        elif _is_num(raw):
            d = serial_to_date(float(raw))
        elif isinstance(raw, str):
            d = parse_date(raw)
        if d is None:
            raise Invalid("data inválida (use dd/mm/aaaa)")
        return d
    if k == "money":
        cents = (
            _money_from_float(float(raw))
            if _is_num(raw)
            else (parse_money(raw) if isinstance(raw, str) else None)
        )
        if cents is None:
            raise Invalid("valor monetário inválido")
        return cents
    n: int | None = None
    if _is_num(raw) and float(raw) == int(raw):
        n = int(raw)
    elif isinstance(raw, str) and _INT.match(raw.strip()):
        n = int(raw.strip())
    if n is None or not col.lo <= n <= col.hi:
        raise Invalid(f"número inteiro entre {col.lo} e {col.hi}")
    return n


def validate(form: dict[str, Any], keys: tuple[str, ...] = EDIT_FIELDS) -> tuple[dict[str, Any], str]:
    """Normalizes ``form`` over the ``keys`` columns; returns (values, errors ``"Col: msg; …"``)."""
    out: dict[str, Any] = {}
    errs: list[str] = []
    for k in keys:
        c = COL[k]
        try:
            out[k] = normalize(c, form.get(k))
        except Invalid as e:
            errs.append(f"{c.header}: {e}")
    return out, "; ".join(errs)


def display(col: Col, v: Any) -> str:
    """Value from the DB for display/export."""
    if v is None:
        return ""
    if col.kind == "date":
        try:
            d = v if isinstance(v, dt.date) else dt.datetime.strptime(str(v), "%Y-%m-%d").date()
        except ValueError:
            return str(v)
        return d.strftime("%d/%m/%Y")
    if col.kind == "money":
        try:
            c = int(v)
        except ValueError:
            return str(v)
        return f"{c // 100},{c % 100:02d}"
    return str(v)


def status_class(s: str) -> str:
    if s in ("Disponível", "Em uso"):
        return "status-active"
    return "status-maintenance" if s == "Manutenção" else "status-draft"


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS assets (
  id bigserial PRIMARY KEY,
  tag text NOT NULL UNIQUE,
  name text NOT NULL,
  category text NOT NULL,
  location text NOT NULL,
  status text NOT NULL,
  brand text,
  model text,
  serial_number text,
  supplier text,
  supplier_cnpj text,
  cost_center text,
  department text,
  responsible text,
  building text,
  room text,
  acquisition_date date,
  acquisition_value_cents bigint,
  residual_value_cents bigint,
  useful_life_months int,
  warranty_until date,
  invoice_number text,
  condition text,
  hostname text,
  os text,
  cpu text,
  ram_gb int,
  storage_gb int,
  ip_address text,
  last_audit date,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS assets_name_trgm ON assets USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS assets_tag_trgm ON assets USING gin (tag gin_trgm_ops);
CREATE INDEX IF NOT EXISTS assets_name ON assets (name, id);
CREATE INDEX IF NOT EXISTS assets_category ON assets (category, name, id);
CREATE INDEX IF NOT EXISTS assets_location ON assets (location, name, id);
CREATE INDEX IF NOT EXISTS assets_status ON assets (status, name, id);
CREATE INDEX IF NOT EXISTS assets_acq ON assets (acquisition_date, id);
CREATE INDEX IF NOT EXISTS assets_value ON assets (acquisition_value_cents, id);
-- Tarefas (importação/exportação), idempotência e outbox: tabelas do hub-sdk
-- (hub_sdk::SCHEMA_SQL), aplicadas junto. A antiga import_jobs não é mais usada.
"""

# Initial load: batches generated inside Postgres itself, no memory used by the app.
SEED_SQL = """
INSERT INTO assets (tag, name, category, location, status, brand, model, serial_number, supplier, supplier_cnpj,
  cost_center, department, responsible, building, room, acquisition_date, acquisition_value_cents,
  residual_value_cents, useful_life_months, warranty_until, invoice_number, condition, hostname, os, cpu,
  ram_gb, storage_gb, ip_address, last_audit, notes)
SELECT
  'PT-' || (2016 + g % 10) || '-' || lpad(g::text, 7, '0'),
  (ARRAY['Notebook Dell Latitude 5440','Notebook Lenovo ThinkPad T14','MacBook Pro 14','Monitor LG UltraWide 29','iPhone 15 Pro','Projetor Epson PowerLite','Impressora HP LaserJet','Cadeira ergonômica Cadence','Furadeira Bosch GSB 13','Toyota Corolla XEi','Ar-condicionado Split 12000','Câmera IP Intelbras'])[1 + g % 12],
  (ARRAY['Tecnologia','Tecnologia','Tecnologia','Tecnologia','Telefonia','Audiovisual','Tecnologia','Mobiliário','Ferramentas','Veículos','Climatização','Segurança'])[1 + g % 12],
  (ARRAY['São Paulo','Rio de Janeiro','Curitiba','Belo Horizonte','Porto Alegre','Recife','Brasília','Remoto'])[1 + (g * 7) % 8],
  (ARRAY['Disponível','Em uso','Em uso','Em uso','Em uso','Manutenção','Baixado'])[1 + (g * 13) % 7],
  (ARRAY['Dell','Lenovo','Apple','LG','Apple','Epson','HP','Cadence','Bosch','Toyota','Midea','Intelbras'])[1 + g % 12],
  (ARRAY['Latitude 5440','ThinkPad T14','MacBook Pro 14','29WQ600','iPhone 15 Pro','PowerLite X49','LaserJet M428','Ergo 300','GSB 13 RE','Corolla XEi','Split 12000','VIP 1230'])[1 + g % 12],
  'SN' || lpad(((g * 2654435761) % 10000000000)::text, 10, '0'),
  (ARRAY['Norte Tecnologia','Logística Horizonte','Casa & Forma','Prime Office','Vetor Ferramentas'])[1 + (g * 3) % 5],
  (ARRAY['12.345.678/0001-90','23.456.789/0001-01','34.567.890/0001-12','45.678.901/0001-23','56.789.012/0001-34'])[1 + (g * 3) % 5],
  'CC-' || (1000 + (g * 17) % 9000),
  (ARRAY['Tecnologia','Financeiro','Comercial','Marketing','Operações','Jurídico','Pessoas','Diretoria'])[1 + (g * 11) % 8],
  (ARRAY['Mariana Costa','Rafael Mendes','Camila Rocha','Lucas Ferreira','John Doe','Ana Beatriz Lima','Pedro Henrique Alves','Juliana Martins','Gustavo Ribeiro','Fernanda Souza'])[1 + (g * 19) % 10],
  'Torre ' || (ARRAY['A','B','C'])[1 + g % 3],
  (1 + g % 19)::text || lpad((1 + g % 39)::text, 2, '0'),
  date '2016-01-01' + ((g * 37) % 3900)::int,
  (8000 + (g * 7919) % 1192000)::int8,
  (800 + (g * 7919) % 119200)::int8,
  12 * (2 + g % 6),
  date '2019-01-01' + ((g * 37) % 3900)::int,
  'NF-' || lpad(((g * 31) % 999999)::text, 6, '0'),
  (ARRAY['Novo','Bom','Bom','Regular','Ruim'])[1 + (g * 23) % 5],
  CASE WHEN g % 12 < 5 THEN 'ws-' || lpad(g::text, 7, '0') END,
  CASE WHEN g % 12 < 5 THEN (ARRAY['Windows 11','macOS 14','Ubuntu 24.04','iOS 17','Windows 10'])[1 + g % 5] END,
  CASE WHEN g % 12 < 5 THEN (ARRAY['Intel i5-1345U','Intel i7-1365U','Apple M3','Apple A17','Intel i5-1235U'])[1 + g % 5] END,
  CASE WHEN g % 12 < 5 THEN (ARRAY[8,16,32])[1 + g % 3] END,
  CASE WHEN g % 12 < 5 THEN (ARRAY[256,512,1024])[1 + g % 3] END,
  CASE WHEN g % 12 < 5 THEN '10.' || (g % 250) || '.' || ((g / 250) % 250) || '.' || (1 + g % 253) END,
  date '2026-01-01' + ((g * 5) % 240)::int,
  CASE WHEN g % 10 = 0 THEN 'Etiqueta substituída na última auditoria.' END
FROM generate_series($1::int8, $2::int8) AS g
ON CONFLICT (tag) DO NOTHING
"""
