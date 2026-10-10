"""Regras puras do Coletor: **sessão de teste em fila** (Folha) e **modos do leitor**
(Escanear). Sem banco nem rede — o app guarda o estado (JSON) em ``col_sessions`` /
``col_prefs`` e chama estas funções dentro de uma transação.

Sessão (uma ativa por usuário):

- :func:`start` embaralha os códigos (``random.Random`` injetável → ordem reproduzível
  nos testes) e mostra o 1º;
- :func:`on_read` compara a leitura com o código **esperado** (normalizado: UPC-A ↔
  EAN-13, maiúsculas): igual → ``ok`` (com "avançar automaticamente" passa ao próximo;
  sem, fica marcado até "Próximo"); diferente → ``erro`` (não avança);
- :func:`next_code` (Próximo: o atual sem leitura certa vira ``pulado``), :func:`pause` /
  :func:`resume` (o tempo parado não conta), :func:`cancel`, :func:`restart` (nova ordem);
- :func:`summary`: acertos, erros, pulados e tempo médio por leitura certa.

Leitor (Escanear): ``interativo`` (depois de uma leitura aceita, espera "Próximo") ou
``automatico`` (pronto de novo depois de ``cooldown_ms``; o mesmo valor dentro de
``DEDUP_MS`` é ignorado) — :func:`reader_gate`.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from coletor.model import normalize_code

RUNNING, PAUSED, DONE, CANCELLED = "running", "paused", "done", "cancelled"
ACTIVE = (RUNNING, PAUSED)
OK, ERRO, PULADO = "ok", "erro", "pulado"

READER_MODES = ("interativo", "automatico")
DEFAULT_READER = "interativo"
COOLDOWNS_MS = (0, 1000, 2000, 3000, 5000)  # 0 = sem pausa (leitor físico)
DEFAULT_COOLDOWN_MS = 2000
#: Janela em que a mesma leitura (o código que ainda está na frente da câmera) é ignorada.
DEDUP_MS = 5000


def match_key(raw: str) -> str:
    """Forma comparável de um código: UPC-A (12) ↔ EAN-13 com zero; 13 dígitos começando
    com 0 também casam o UPC-A; maiúsculas, sem espaços."""
    return normalize_code(raw)


@dataclass
class Code:
    fmt: str
    value: str  # texto que vai no símbolo (o hub desenha)
    expected: str  # forma comparável do que a câmera devolve
    label: str


@dataclass
class Session:
    id: str
    seed: int
    codes: list[Code]
    auto: bool = True
    pos: int = 0
    status: str = RUNNING
    matched: bool = False
    started_at: float = 0.0
    shown_at: float = 0.0
    paused_at: float | None = None
    finished_at: float | None = None
    last_error: dict[str, str] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    # ---- (de)serialização (jsonb) -----------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Session:
        d = dict(d)
        d["codes"] = [Code(**c) for c in d.get("codes", [])]
        d["results"] = list(d.get("results", []))
        return cls(**d)

    # ---- leitura -------------------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.codes)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def current(self) -> Code | None:
        return self.codes[self.pos] if self.active and self.pos < len(self.codes) else None

    def order(self) -> list[str]:
        return [c.expected for c in self.codes]


def start(codes: list[Code], rng: random.Random, now: float, auto: bool = True, sid: str = "") -> Session:
    seed = rng.randrange(1 << 30)
    shuffled = list(codes)
    random.Random(seed).shuffle(shuffled)  # noqa: S311 — ordem de teste, não segredo
    return Session(
        id=sid or f"s{seed:x}",
        seed=seed,
        codes=shuffled,
        auto=auto,
        started_at=now,
        shown_at=now,
    )


def _elapsed_ms(s: Session, now: float) -> int:
    return max(0, round((now - s.shown_at) * 1000))


def _advance(s: Session, now: float) -> None:
    s.pos += 1
    s.matched = False
    s.last_error = None
    s.shown_at = now
    if s.pos >= len(s.codes):
        s.status = DONE
        s.finished_at = now


def on_read(s: Session, raw: str, now: float) -> str:
    """Aplica uma leitura do celular: ``ok``, ``erro`` ou ``ignorado`` (sessão parada,
    terminada ou o código atual já certo esperando "Próximo")."""
    cur = s.current()
    if cur is None or s.status != RUNNING or s.matched:
        return "ignorado"
    got = match_key(raw)
    entry = {"pos": s.pos, "value": cur.value, "fmt": cur.fmt, "read": raw[:64], "at": now}
    if got == cur.expected:
        s.results.append(entry | {"outcome": OK, "ms": _elapsed_ms(s, now)})
        if s.auto:
            _advance(s, now)
        else:
            s.matched = True
            s.last_error = None
        return OK
    s.results.append(entry | {"outcome": ERRO, "ms": _elapsed_ms(s, now)})
    s.last_error = {"read": raw[:64], "expected": cur.value}
    return ERRO


def next_code(s: Session, now: float) -> bool:
    """Próximo: o atual sem leitura certa vira ``pulado``. Parada → continua parada no
    próximo código (o tempo parado não conta)."""
    cur = s.current()
    if cur is None:
        return False
    if not s.matched:
        s.results.append(
            {
                "pos": s.pos,
                "value": cur.value,
                "fmt": cur.fmt,
                "read": "",
                "at": now,
                "outcome": PULADO,
                "ms": 0,
            }
        )
    _advance(s, now)
    if s.status == PAUSED:
        s.paused_at = now
    return True


def pause(s: Session, now: float) -> bool:
    if s.status != RUNNING:
        return False
    s.status = PAUSED
    s.paused_at = now
    return True


def resume(s: Session, now: float) -> bool:
    if s.status != PAUSED:
        return False
    s.shown_at += now - (s.paused_at or now)
    s.paused_at = None
    s.status = RUNNING
    return True


def cancel(s: Session, now: float) -> bool:
    if not s.active:
        return False
    s.status = CANCELLED
    s.finished_at = now
    return True


def restart(s: Session, rng: random.Random, now: float) -> Session:
    """Mesmos códigos, nova ordem, resultados zerados (mantém "avançar automaticamente")."""
    base = sorted(s.codes, key=lambda c: (c.fmt, c.expected))
    return start(base, rng, now, auto=s.auto)


def set_auto(s: Session, on: bool) -> None:
    s.auto = on


@dataclass(frozen=True)
class Summary:
    acertos: int
    erros: int
    pulados: int
    avg_ms: int
    total_ms: int


def summary(s: Session) -> Summary:
    oks = [r for r in s.results if r["outcome"] == OK]
    end = s.finished_at if s.finished_at is not None else s.shown_at
    return Summary(
        acertos=len(oks),
        erros=sum(1 for r in s.results if r["outcome"] == ERRO),
        pulados=sum(1 for r in s.results if r["outcome"] == PULADO),
        avg_ms=round(sum(r["ms"] for r in oks) / len(oks)) if oks else 0,
        total_ms=max(0, round((end - s.started_at) * 1000)),
    )


# ---- modos do leitor (Escanear) -------------------------------------------------------------
@dataclass
class Reader:
    mode: str = DEFAULT_READER
    cooldown_ms: int = DEFAULT_COOLDOWN_MS
    hold: bool = False  # interativo: leitura aceita esperando "Próximo"
    last_value: str = ""
    last_at: float = 0.0


def reader_gate(r: Reader, raw: str, now: float) -> str:
    """Decide uma leitura antes de registrar: ``aceita``, ``aguardando`` (interativo
    esperando "Próximo"), ``pausa`` (automático, dentro da pausa) ou ``repetida`` (o mesmo
    valor dentro de ``DEDUP_MS``). Aceita → atualiza o estado."""
    key = match_key(raw)
    since_ms = (now - r.last_at) * 1000
    if r.mode == "interativo":
        if r.hold:
            return "aguardando"
    else:
        if r.last_value and key == r.last_value and since_ms < DEDUP_MS:
            return "repetida"
        if r.cooldown_ms and r.last_at and since_ms < r.cooldown_ms:
            return "pausa"
    r.last_value = key
    r.last_at = now
    r.hold = r.mode == "interativo"
    return "aceita"


def reader_next(r: Reader) -> None:
    """Interativo: "Próximo" libera a próxima leitura."""
    r.hold = False


def clean_reader_mode(v: str) -> str:
    return v if v in READER_MODES else DEFAULT_READER


def clean_cooldown(v: str | int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_MS
    return n if n in COOLDOWNS_MS else DEFAULT_COOLDOWN_MS
