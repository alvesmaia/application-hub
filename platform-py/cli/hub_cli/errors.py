"""Erros da CLI com código de saída estável: 0 ok, 1 falha, 2 validação, 3 autenticação, 4 hub inacessível."""

OK, FAIL, VALIDATION, AUTH, UNREACHABLE = 0, 1, 2, 3, 4


class CliError(Exception):
    def __init__(self, message: str, code: int = FAIL) -> None:
        super().__init__(message)
        self.code = code
