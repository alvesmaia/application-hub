"""CLI errors with stable exit codes: 0 ok, 1 failure, 2 validation, 3 authentication, 4 hub unreachable."""

OK, FAIL, VALIDATION, AUTH, UNREACHABLE = 0, 1, 2, 3, 4


class CliError(Exception):
    def __init__(self, message: str, code: int = FAIL) -> None:
        super().__init__(message)
        self.code = code
