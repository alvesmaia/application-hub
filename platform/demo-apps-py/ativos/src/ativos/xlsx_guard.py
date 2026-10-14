"""Zip bomb guard for the uploaded ``.xlsx``: rules over the central directory and over the REAL
uncompressed sizes, checked before calamine opens the file."""

from __future__ import annotations

import os
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from hub_sdk.jobs import JobFail


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Zip rules (central directory, without decompressing anything):
MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_TOTAL_UNCOMPRESSED = 2560 * 1024 * 1024
MAX_ZIP_ENTRY_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_RATIO = 100
# Current limitation: the whole sheet becomes memory (no streaming read), so the cap is
# smaller (default 300 MiB ≈ 460k simple rows).
MAX_ZIP_SHEET_UNCOMPRESSED = _env_int("ATIVOS_IMPORT_MAX_SHEET_MB", 300) * 1024 * 1024


def check_zip_entries(entries: list[tuple[str, int, int]]) -> None:
    """Rules about (name, compressed, uncompressed) of each entry."""
    if len(entries) > MAX_ZIP_ENTRIES:
        raise JobFail(
            f"Arquivo .xlsx inválido: {len(entries)} itens no arquivo compactado (limite {MAX_ZIP_ENTRIES})."
        )
    total = 0
    for name, compressed, uncompressed in entries:
        is_sheet = name.startswith("xl/worksheets/") and name.endswith(".xml")
        entry_max = MAX_ZIP_SHEET_UNCOMPRESSED if is_sheet else MAX_ZIP_ENTRY_UNCOMPRESSED
        if uncompressed > entry_max:
            raise JobFail(
                f"Arquivo .xlsx inválido: um item do arquivo descomprime para mais de {entry_max >> 20} MB."
            )
        total += uncompressed
        if total > MAX_ZIP_TOTAL_UNCOMPRESSED:
            raise JobFail(
                f"Arquivo .xlsx inválido: o conteúdo descomprimido passa de {MAX_ZIP_TOTAL_UNCOMPRESSED >> 20} MB."
            )
        if (
            compressed > 0
            and uncompressed > 1024 * 1024
            and uncompressed // max(compressed, 1) > MAX_ZIP_RATIO
        ):
            raise JobFail(
                "Arquivo .xlsx inválido: taxa de compressão suspeita em um item do arquivo "
                "(possível arquivo malicioso)."
            )
    if not any(n.startswith("xl/worksheets/") for n, _, _ in entries):
        raise JobFail("O arquivo não tem nenhuma planilha.")


INVALID = "O arquivo não é uma planilha .xlsx válida."
_LOCAL = struct.Struct("<4s5H3L2H")  # zip local header (30 bytes)
_INFLATE_CHUNK = 256 * 1024


def _inflated_size(f: Any, method: int, compressed: int, cap: int) -> int:
    """REAL uncompressed size of an entry (reading the bytes, not what the zip declares),
    stopping as soon as it exceeds ``cap`` (returns ``cap + 1``). Constant memory."""
    if method == zipfile.ZIP_STORED:
        return compressed
    if method != zipfile.ZIP_DEFLATED:
        raise JobFail("Arquivo .xlsx inválido: método de compressão não suportado.")
    d = zlib.decompressobj(-15)
    left = compressed
    total = 0
    while left > 0 and not d.eof:
        chunk = f.read(min(_INFLATE_CHUNK, left))
        if not chunk:
            break
        left -= len(chunk)
        data = chunk
        while data and not d.eof:  # ty: ignore[redundant-condition-strict]  # typeshed hardcodes `eof` to False; it changes at the end of the stream
            out = d.decompress(data, _INFLATE_CHUNK)
            total += len(out)
            if total > cap:
                return cap + 1
            data = d.unconsumed_tail
    return total


def actual_entries(path: Path) -> list[tuple[str, int, int]]:
    """(name, compressed, REAL uncompressed) of each entry. The central directory (and the
    local header) belong to the file's author: a zip can declare 1 KB and decompress to
    gigabytes. Here each entry is decompressed as a stream and counted, with a cap — which is exactly
    what calamine will read afterwards."""
    try:
        with zipfile.ZipFile(path) as z, path.open("rb") as f:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                check_zip_entries([(i.filename, 0, 0) for i in infos])
            out: list[tuple[str, int, int]] = []
            total = 0
            for i in infos:
                is_sheet = i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml")
                entry_max = MAX_ZIP_SHEET_UNCOMPRESSED if is_sheet else MAX_ZIP_ENTRY_UNCOMPRESSED
                cap = max(0, min(entry_max, MAX_ZIP_TOTAL_UNCOMPRESSED - total))
                f.seek(i.header_offset)
                h = f.read(_LOCAL.size)
                if len(h) < _LOCAL.size or h[:4] != b"PK\x03\x04":
                    raise JobFail(INVALID)
                name_len, extra_len = _LOCAL.unpack(h)[-2:]
                f.seek(i.header_offset + _LOCAL.size + name_len + extra_len)
                real = _inflated_size(f, i.compress_type, i.compress_size, cap)
                out.append((i.filename, i.compress_size, max(real, i.file_size)))
                total += real
                if real > cap:
                    break  # check_zip_entries refuses with the right message
            return out
    except (zipfile.BadZipFile, zlib.error, OSError, struct.error, EOFError) as e:
        raise JobFail(INVALID) from e


def check_zip(path: Path) -> None:
    """Before handing the file to calamine: rules over the central directory
    (fast) and then over the REAL uncompressed sizes (:func:`actual_entries`)."""
    try:
        with zipfile.ZipFile(path) as z:
            entries = [(i.filename, i.compress_size, i.file_size) for i in z.infolist()]
    except (zipfile.BadZipFile, OSError) as e:
        raise JobFail(INVALID) from e
    check_zip_entries(entries)
    check_zip_entries(actual_entries(path))
