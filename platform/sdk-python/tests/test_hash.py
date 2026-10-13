from hub_sdk.idempotency import RequestHasher, request_hash, split_content_type, storable, valid_key

MP = "multipart/form-data; boundary="

# Vetores de hash (formato estável: não mude sem migração).
HASH_VECTORS = [
    (
        ("POST", "/x", "", MP + "AAA111", b"--AAA111\r\nconteudo\r\n--AAA111--"),
        "035fbd2375bd0dc7b330332fbf79dff42945b24a42516b01594307f2a34d06f5",
    ),
    (
        ("POST", "/ativos/itens/7", "a=1", "application/x-www-form-urlencoded", b"name=Notebook&version=3"),
        "d8091058dac88ffce55c3562fd8459ad15983c8ded3801e943e032eadc730b88",
    ),
]


def test_hash_matches_vectors() -> None:
    for args, want in HASH_VECTORS:
        assert request_hash(*args) == want


def test_boundary_is_normalized_but_content_counts() -> None:
    a = request_hash("POST", "/x", "", MP + "AAA111", b"--AAA111\r\nconteudo\r\n--AAA111--")
    b = request_hash("POST", "/x", "", MP + '"BBB222"', b"--BBB222\r\nconteudo\r\n--BBB222--")
    c = request_hash("POST", "/x", "", MP + "BBB222", b"--BBB222\r\noutro\r\n--BBB222--")
    assert a == b != c
    assert request_hash("POST", "/y", "", "", b"a=1") != request_hash("POST", "/y", "", "", b"a=2")


def test_chunked_equals_whole_for_every_cut() -> None:
    body = b"--BBB222\nconteudo grande\n--BBB222--"
    ct = MP + "BBB222"
    whole = request_hash("POST", "/x", "", ct, body)
    for cut in range(len(body) + 1):
        h = RequestHasher("POST", "/x", "", ct)
        h.update(body[:cut])
        h.update(body[cut:])
        assert h.finish() == whole, cut


def test_big_body_fully_hashed() -> None:
    big1 = b"a" * (3 << 20)
    big2 = bytearray(big1)
    big2[2 << 20] = ord("b")
    assert request_hash("POST", "/y", "", "", big1) != request_hash("POST", "/y", "", "", bytes(big2))


def test_key_rules_and_storable() -> None:
    assert valid_key("0b1f6c1e-3c1a-4a7e-9f7e-7d5d3c2b1a00")
    assert not valid_key("curta")
    assert not valid_key("x" * 129)
    assert not valid_key("com espaço aqui")
    assert storable(201) and storable(409) and storable(422)
    assert not storable(429) and not storable(503) and not storable(408)
    assert split_content_type(MP + '"x"') == ("multipart/form-data", "x")
