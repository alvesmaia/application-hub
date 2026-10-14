"""Supplier directory of the mockup, attachment validation and object names."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    category: str
    city: str
    status: str
    cnpj: str


SUPPLIERS = (
    Supplier("FOR-0284", "Norte Tecnologia", "Tecnologia", "São Paulo, SP", "Aprovado", "12.345.678/0001-90"),
    Supplier(
        "FOR-0279", "Logística Horizonte", "Logística", "Campinas, SP", "Em avaliação", "23.456.789/0001-01"
    ),
    Supplier("FOR-0271", "Casa & Forma", "Facilities", "Curitiba, PR", "Aprovado", "34.567.890/0001-12"),
    Supplier(
        "FOR-0268",
        "VerdeVivo Refeições",
        "Alimentação",
        "São Paulo, SP",
        "Documentação",
        "45.678.901/0001-23",
    ),
    Supplier(
        "FOR-0252", "Atlas Segurança", "Serviços", "Rio de Janeiro, RJ", "Aprovado", "56.789.012/0001-34"
    ),
    Supplier(
        "FOR-0246",
        "Papelaria Central",
        "Suprimentos",
        "Belo Horizonte, MG",
        "Em avaliação",
        "67.890.123/0001-45",
    ),
)


def supplier(code: str) -> Supplier | None:
    return next((s for s in SUPPLIERS if s.code == code), None)


# (extension, content-type, signature at the start of the file)
TYPES = (
    ("pdf", "application/pdf", b"%PDF-"),
    ("png", "image/png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", "image/jpeg", b"\xff\xd8\xff"),
    ("jpeg", "image/jpeg", b"\xff\xd8\xff"),
    ("webp", "image/webp", b"RIFF"),
)


def check_type(file_name: str, declared: str, head: bytes) -> str:
    """Checks extension, declared content-type and signature; returns the content-type or raises
    ``ValueError`` with the message for the user."""
    ext = file_name.rsplit(".", 1)[-1].lower()
    hit = next((t for t in TYPES if t[0] == ext), None)
    if hit is None:
        raise ValueError("Tipo de arquivo não permitido: envie PDF, PNG, JPEG ou WebP.")
    _, ct, magic = hit
    dec = declared.split(";")[0].strip().lower()
    if dec and dec != "application/octet-stream" and dec != ct:
        raise ValueError(f"O tipo informado ({dec}) não corresponde à extensão .{ext}.")
    webp_ok = ext != "webp" or head[8:12] == b"WEBP"
    if not head.startswith(magic) or not webp_ok:
        raise ValueError(f"O conteúdo do arquivo não é um .{ext} válido.")
    return ct


_FOLD = str.maketrans("áàâãÁÀÂÃéêÉÊíÍóôõÓÔÕúÚçÇ", "aaaaaaaaeeeeiioooooouucc")


def safe_name(s: str) -> str:
    """Safe name for the object key: letters, digits, ``.``, ``-``, ``_``."""
    base = s.replace("\\", "/").rsplit("/", 1)[-1]
    out = "".join(c if (c.isascii() and c.isalnum()) or c in ".-_" else "_" for c in base.translate(_FOLD))
    return out.lstrip(".")[:100] or "arquivo"


def valid_object_name(n: str) -> bool:
    return (
        0 < len(n) <= 140
        and not n.startswith(".")
        and all((c.isascii() and c.isalnum()) or c in ".-_" for c in n)
    )


def attach_max_bytes() -> int:
    try:
        mb = int(
            os.environ.get("HUB_ATTACH_MAX_MB_FORNECEDORES") or os.environ.get("HUB_ATTACH_MAX_MB") or 20
        )
    except ValueError:
        mb = 20
    return mb * 1024 * 1024
