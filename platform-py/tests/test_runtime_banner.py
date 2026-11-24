"""admin/runtime: the "runtime unavailable" banner names the likely cause in plain words, not the raw exception."""

import pytest

from hub.ops.runtime_view import runtime_banner
from hub.ux.texts import tr

RAW_MISSING = "Error while fetching server API version: ('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (RAW_MISSING, "o socket do runtime não existe no contêiner do hub"),
        (
            "('Connection aborted.', PermissionError(13, 'Permission denied'))",
            "o hub não tem permissão no socket",
        ),
        (
            "('Connection aborted.', ConnectionRefusedError(111, 'Connection refused'))",
            "o runtime recusou a conexão",
        ),
        (
            "UnixHTTPConnectionPool(host='localhost', port=None): Read timed out. (read timeout=5)",
            "não respondeu a tempo",
        ),
    ],
)
def test_known_causes_in_plain_words(error: str, cause: str) -> None:
    text = runtime_banner(error)
    assert cause in text
    assert "FileNotFoundError" not in text and "Connection aborted" not in text
    assert "a tela mostra a configuração salva" in text


def test_unknown_error_keeps_a_short_detail() -> None:
    text = runtime_banner("boom: engine exploded")
    assert "boom: engine exploded" in text


def test_every_banner_has_an_english_version() -> None:
    for e in (RAW_MISSING, "Permission denied", "Connection refused", "timed out", "boom"):
        pt = runtime_banner(e)
        assert tr(pt, "en") != pt, pt
