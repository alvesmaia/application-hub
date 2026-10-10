"""Regras puras da sessão de teste em fila e dos modos do leitor (:mod:`coletor.fila`)."""

from __future__ import annotations

import random

from coletor import fila
from coletor.app import N_SESSION_CODES, session_codes


def codes() -> list[fila.Code]:
    return session_codes()


def test_session_has_24_distinct_codes_with_full_values() -> None:
    cs = codes()
    assert len(cs) == N_SESSION_CODES == 24
    assert len({c.expected for c in cs}) == 24, "sem códigos repetidos"
    by = {c.fmt: c for c in cs}
    assert by["upc_a"].value == "036000291452" and by["upc_a"].expected == "0036000291452"
    assert by["ean_8"].value == "96385074"
    assert {"ean_13", "code_39", "qr_code", "code_128", "data_matrix"} <= set(by)


def test_shuffle_is_seedable_and_restart_reshuffles() -> None:
    a = fila.start(codes(), random.Random(7), now=0)
    b = fila.start(codes(), random.Random(7), now=0)
    assert a.order() == b.order(), "mesma semente → mesma ordem"
    assert sorted(a.order()) == sorted(c.expected for c in codes())
    assert a.order() != [c.expected for c in codes()], "embaralhado"
    rng = random.Random(7)
    s = fila.start(codes(), rng, now=0)
    fila.on_read(s, s.codes[0].value, now=1)
    r = fila.restart(s, rng, now=2)
    assert r.order() != s.order() and sorted(r.order()) == sorted(s.order())
    assert r.pos == 0 and r.results == [] and r.status == fila.RUNNING and r.auto == s.auto


def test_match_advances_with_auto_and_mismatch_does_not() -> None:
    s = fila.start(codes(), random.Random(1), now=100.0, auto=True)
    first, second = s.codes[0], s.codes[1]
    assert fila.on_read(s, "NADA-1", now=101.0) == fila.ERRO
    assert s.pos == 0 and s.last_error == {"read": "NADA-1", "expected": first.value}
    assert fila.on_read(s, first.value.lower(), now=102.5) == fila.OK
    assert s.pos == 1 and s.current() == second and s.last_error is None
    oks = [r for r in s.results if r["outcome"] == fila.OK]
    assert oks[0]["ms"] == 2500


def test_upc_a_matches_as_ean13_from_the_camera() -> None:
    s = fila.start(codes(), random.Random(3), now=0)
    upc = next(i for i, c in enumerate(s.codes) if c.fmt == "upc_a")
    s.pos = upc
    assert fila.on_read(s, "0036000291452", now=1) == fila.OK  # o nativo às vezes devolve EAN-13


def test_without_auto_the_match_waits_for_next() -> None:
    s = fila.start(codes(), random.Random(2), now=0, auto=False)
    assert fila.on_read(s, s.codes[0].value, now=1) == fila.OK
    assert s.pos == 0 and s.matched
    assert fila.on_read(s, s.codes[1].value, now=2) == "ignorado", "já certo: espera Próximo"
    assert fila.next_code(s, now=3) and s.pos == 1 and not s.matched
    assert [r["outcome"] for r in s.results] == [fila.OK]


def test_next_skips_pause_resume_and_summary() -> None:
    s = fila.start(codes(), random.Random(4), now=0, auto=True)
    assert fila.next_code(s, now=1)  # pulado
    assert fila.pause(s, now=2) and s.status == fila.PAUSED
    assert fila.on_read(s, s.codes[1].value, now=3) == "ignorado", "parada não lê"
    assert fila.resume(s, now=12)  # 10 s parado não contam
    assert fila.on_read(s, s.codes[1].value, now=13) == fila.OK
    assert s.results[-1]["ms"] == 2000
    for i in range(2, s.total):
        fila.on_read(s, "ERRADO", now=14 + i)
        fila.on_read(s, s.codes[i].value, now=15 + i)
    assert s.status == fila.DONE and s.current() is None
    sm = fila.summary(s)
    assert (sm.acertos, sm.pulados, sm.erros) == (s.total - 1, 1, s.total - 2)
    assert sm.avg_ms > 0 and sm.total_ms > 0
    assert not fila.next_code(s, now=99) and not fila.pause(s, now=99)


def test_cancel_and_json_roundtrip() -> None:
    s = fila.start(codes(), random.Random(5), now=0)
    fila.on_read(s, "X", now=1)
    back = fila.Session.from_json(s.to_json())
    assert back == s
    assert fila.cancel(back, now=2) and back.status == fila.CANCELLED and not back.active
    assert not fila.cancel(back, now=3)


def test_reader_interactive_holds_until_next() -> None:
    r = fila.Reader(mode="interativo")
    assert fila.reader_gate(r, "COL-0001", 0) == "aceita" and r.hold
    assert fila.reader_gate(r, "COL-0002", 10) == "aguardando"
    fila.reader_next(r)
    assert fila.reader_gate(r, "COL-0002", 11) == "aceita"


def test_reader_automatic_cooldown_and_dedup() -> None:
    r = fila.Reader(mode="automatico", cooldown_ms=2000)
    assert fila.reader_gate(r, "COL-0001", 100.0) == "aceita" and not r.hold
    assert fila.reader_gate(r, "COL-0002", 101.0) == "pausa", "dentro dos 2 s"
    assert fila.reader_gate(r, "COL-0001", 103.0) == "repetida", "o mesmo dentro de 5 s"
    assert fila.reader_gate(r, "COL-0002", 103.0) == "aceita"
    assert fila.reader_gate(r, "col-0001", 106.0) == "aceita", "outro valor antes; passou a pausa"
    assert fila.reader_gate(r, "COL-0001", 111.5) == "aceita", "fora da janela de 5 s"
    z = fila.Reader(mode="automatico", cooldown_ms=0)
    assert fila.reader_gate(z, "A1", 0) == "aceita" and fila.reader_gate(z, "B2", 0.1) == "aceita"
    assert fila.reader_gate(z, "B2", 0.2) == "repetida"


def test_clean_reader_inputs() -> None:
    assert (
        fila.clean_reader_mode("x") == "interativo" and fila.clean_reader_mode("automatico") == "automatico"
    )
    assert fila.clean_cooldown("3000") == 3000 and fila.clean_cooldown("7") == 2000
    assert fila.clean_cooldown("abc") == 2000 and fila.clean_cooldown(0) == 0
