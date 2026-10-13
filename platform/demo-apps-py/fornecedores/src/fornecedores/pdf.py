"""Gerador mínimo de PDF 1.4: texto Helvetica
(WinAnsi), retângulos e linhas, várias páginas A4."""

from __future__ import annotations

A4_W = 595.0
A4_H = 842.0
RGB = tuple[float, float, float]

_EXTRA = {"€": 0x80, "‘": 0x91, "’": 0x92, "“": 0x93, "”": 0x94, "•": 0x95, "–": 0x96, "—": 0x97, "…": 0x85}


def _num(v: float) -> str:
    return repr(float(v)).removesuffix(".0")


def win_ansi(s: str) -> bytes:
    out = bytearray()
    for c in s:
        o = ord(c)
        out.append(o if 0x20 <= o <= 0x7E or 0xA0 <= o <= 0xFF else _EXTRA.get(c, 0x3F))
    return bytes(out)


def pdf_string(s: str) -> str:
    out = ["("]
    for b in win_ansi(s):
        if b in b"()\\":
            out.append("\\" + chr(b))
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03o}")
    out.append(")")
    return "".join(out)


class Pdf:
    def __init__(self) -> None:
        self.pages: list[str] = []
        self.cur: list[str] = []

    def new_page(self) -> None:
        if self.cur:
            self.pages.append("".join(self.cur))
            self.cur = []

    def text(self, x: float, y: float, size: float, bold: bool, rgb: RGB, s: str) -> None:
        font = "F2" if bold else "F1"
        r, g, b = (_num(v) for v in rgb)
        self.cur.append(
            f"BT /{font} {_num(size)} Tf {r} {g} {b} rg {_num(x)} {_num(A4_H - y)} Td {pdf_string(s)} Tj ET\n"
        )

    def rect(self, x: float, y: float, w: float, h: float, rgb: RGB) -> None:
        r, g, b = (_num(v) for v in rgb)
        self.cur.append(f"{r} {g} {b} rg {_num(x)} {_num(A4_H - y - h)} {_num(w)} {_num(h)} re f\n")

    def line(self, x1: float, y1: float, x2: float, y2: float, rgb: RGB) -> None:
        r, g, b = (_num(v) for v in rgb)
        self.cur.append(
            f"{r} {g} {b} RG 0.7 w {_num(x1)} {_num(A4_H - y1)} m {_num(x2)} {_num(A4_H - y2)} l S\n"
        )

    def finish(self) -> bytes:
        self.new_page()
        pages = self.pages or [""]
        n = len(pages)
        objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
        kids = " ".join(f"{5 + i * 2} 0 R" for i in range(n))
        objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for i, content in enumerate(pages):
            objs.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_num(A4_W)} {_num(A4_H)}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + i * 2} 0 R >>".encode()
            )
            body = content.encode("latin-1")
            objs.append(f"<< /Length {len(body)} >>\nstream\n".encode() + body + b"\nendstream")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs):
            offsets.append(len(out))
            out += f"{i + 1} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
