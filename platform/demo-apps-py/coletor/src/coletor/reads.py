"""Readings: what a scanned value is (item, location, order, link), storing it, the user's reader
preferences and the camera modes."""

from __future__ import annotations

import datetime as dt
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from hub_sdk import outbox
from hub_sdk.locale import t

from coletor import barcodes, fila
from coletor.live import notify
from coletor.model import guess_format, is_url, normalize_code
from coletor.state import public_url, user_login, user_name

# Camera modes (Scan toggle) → formats of `data-hub-scan-formats`.
MODES: dict[str, tuple[str, str]] = {
    "barras": ("ean_13,ean_8,upc_a,upc_e,code_128,code_39,itf", "Modo código de barras — troque para QR"),
    "qr": ("qr_code,data_matrix", "Modo QR — troque para Código de barras"),
}
DEFAULT_MODE = "barras"
MODE_KEYS = ("barras", "qr")
MODE_LABELS = {"barras": "Código de barras", "qr": "QR"}
SOURCES = {"camera": "câmera", "leitor": "coletor físico", "api": "API"}
FORMAT_LABELS = barcodes.LABELS | {"upc_e": "UPC-E", "itf": "ITF", "pdf417": "PDF417", "codabar": "Codabar"}
MAX_VALUE = 256


async def find_item(c: Any, code: str) -> Any:
    return await c.fetchrow("SELECT * FROM col_items WHERE code = $1 OR code128 = $1", code)


def clean_value(raw: str) -> str:
    return "".join(ch for ch in str(raw) if ch.isprintable()).strip()[:MAX_VALUE]


def _link_label(value: str) -> str:
    parts = urlsplit(value.strip())
    own = "coletor" in parts.path or parts.netloc.endswith(urlsplit(public_url()).netloc or "\x00")
    return t("Link para o app — abre o Coletor") if own else t("Link (endereço web), não um código")


async def classify(c: Any, raw: str) -> tuple[str, str]:
    """(type, label) of a reading: item, location, order, link or unknown."""
    if is_url(raw):
        return "link", _link_label(raw)
    code = normalize_code(raw)
    if not code:
        return "desconhecido", t("Código desconhecido")
    if c is not None:
        item = await find_item(c, code)
        if item is not None:
            return "item", t("Item: {n}", n=item["name"])
        loc = await c.fetchrow("SELECT code, name FROM col_locations WHERE code = $1", code)
        if loc is not None:
            return "local", t("Local: {c} · {n}", c=loc["code"], n=loc["name"])
        o = await c.fetchrow("SELECT code, supplier FROM col_orders WHERE code = $1", code)
        if o is not None:
            return "pedido", t("Pedido {c} · {s}", c=o["code"], s=o["supplier"])
    return "desconhecido", t("Código desconhecido")


def feedback_of(kind: str) -> str:
    return {"item": "ok", "local": "ok", "pedido": "ok", "link": "warn"}.get(kind, "error")


def read_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["feedback"] = feedback_of(d["kind"])
    d["format_label"] = FORMAT_LABELS.get(d["format"], d["format"])
    d["source_label"] = t(str(SOURCES.get(d["source"]) or d["source"]))
    at = d.get("at")
    d["time"] = at.astimezone().strftime("%H:%M:%S") if isinstance(at, dt.datetime) else ""
    return d


async def recent_reads(c: Any, user: str, limit: int) -> list[dict[str, Any]]:
    rows = await c.fetch(
        "SELECT id, value, format, source, kind, label, at FROM col_reads WHERE user_name = $1 "
        "ORDER BY id DESC LIMIT $2",
        user,
        limit,
    )
    return [read_row(r) for r in rows]


async def store_read(c: Any, req: Request, value: str, fmt: str, source: str) -> dict[str, Any]:
    kind, label = await classify(c, value)
    row = await c.fetchrow(
        "INSERT INTO col_reads (user_name, value, format, source, kind, label) VALUES ($1,$2,$3,$4,$5,$6) "
        "RETURNING id, value, format, source, kind, label, at",
        user_login(req),
        value,
        fmt,
        source,
        kind,
        label,
    )
    await outbox.write(c, outbox.audit(user_name(req), "Leitura registrada", value[:64], f"{fmt} · {label}"))
    await notify(c, user_login(req), "read")
    return read_row(row)


async def scan_mode(c: Any, user: str) -> str:
    m = await c.fetchval("SELECT scan_mode FROM col_prefs WHERE user_name = $1", user) if c else None
    return m if isinstance(m, str) and m in MODES else DEFAULT_MODE


async def load_reader(c: Any, user: str, lock: bool = False) -> fila.Reader:
    row = await c.fetchrow(
        "SELECT reader_mode, cooldown_ms, hold, last_value, last_at FROM col_prefs WHERE user_name = $1"  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        + (" FOR UPDATE" if lock else ""),
        user,
    )
    if row is None:
        return fila.Reader()
    return fila.Reader(
        mode=fila.clean_reader_mode(row["reader_mode"]),
        cooldown_ms=fila.clean_cooldown(row["cooldown_ms"]),
        hold=bool(row["hold"]),
        last_value=row["last_value"],
        last_at=float(row["last_at"]),
    )


async def save_reader(c: Any, user: str, rd: fila.Reader) -> None:
    await c.execute(
        "INSERT INTO col_prefs (user_name, reader_mode, cooldown_ms, hold, last_value, last_at) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_name) DO UPDATE SET reader_mode = $2, "
        "cooldown_ms = $3, hold = $4, last_value = $5, last_at = $6",
        user,
        rd.mode,
        rd.cooldown_ms,
        rd.hold,
        rd.last_value,
        rd.last_at,
    )


def mode_ctx(mode: str, rd: fila.Reader | None = None) -> dict[str, Any]:
    formats, wrong = MODES[mode]
    rd = rd or fila.Reader()
    return {
        "mode": mode,
        "formats": formats,
        "wrong_hint": t(wrong),
        "mode_formats": "|".join(MODES[k][0] for k in MODE_KEYS),
        "mode_hints": "|".join(t(MODES[k][1]).replace("|", "/") for k in MODE_KEYS),
        "mode_labels": "|".join(t(MODE_LABELS[k]) for k in MODE_KEYS),
        "mode_keys": "|".join(MODE_KEYS),
        "reader": rd,
        "cooldowns": fila.COOLDOWNS_MS,
        "dedup_ms": fila.DEDUP_MS,
    }


def read_format(form: dict[str, str], value: str) -> tuple[str, str]:
    """(format, source): with a format coming from the camera (hub) → camera; without one → physical reader."""
    f = form.get("formato", "").strip().lower()
    if f and f.replace("_", "").isalnum() and len(f) <= 16:
        return f, "camera"
    return guess_format(value), "leitor"
