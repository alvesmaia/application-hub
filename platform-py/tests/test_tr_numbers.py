"""English pages show English numbers: a pt-BR number filled into a catalog template (decimal comma, dot thousands)
is converted; texts the catalog lacked before (status "hoje", backups "N arquivos retidos") are translated."""

import pytest

from hub.ux.texts import tr


@pytest.mark.parametrize(
    ("pt", "en"),
    [
        ("100,0% em 30 dias", "100.0% in 30 days"),
        ("99,95% em 30 dias", "99.95% in 30 days"),
        ("hoje", "today"),
        ("1 arquivo retido", "1 file retained"),
        ("12 arquivos retidos", "12 files retained"),
    ],
)
def test_english(pt: str, en: str) -> None:
    assert tr(pt, "en") == en


def test_versions_and_plain_values_are_not_numbers_to_convert() -> None:
    assert tr("Versão 2.5.0", "en").endswith("2.5.0")
