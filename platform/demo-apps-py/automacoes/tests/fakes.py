"""In-memory stand-ins for the external services (never the real network)."""

from __future__ import annotations

import httpx

SE = {
    "cep": "01001-000",
    "logradouro": "Praça da Sé",
    "complemento": "lado ímpar",
    "bairro": "Sé",
    "localidade": "São Paulo",
    "uf": "SP",
    "ibge": "3550308",
    "ddd": "11",
    "siafi": "7107",
}
HOLIDAYS_2026 = [
    {"date": "2026-01-01", "name": "Confraternização mundial", "type": "national"},
    {"date": "2026-04-21", "name": "Tiradentes", "type": "national"},
]


class FakeApis:
    """ViaCEP + BrasilAPI. ``unavailable`` = how many of the next calls answer 503 (then they
    recover); ``calls`` = every URL requested."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.unavailable = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(str(req.url))
        if self.unavailable > 0:
            self.unavailable -= 1
            return httpx.Response(503, text="indisponível")
        if req.url.host == "viacep.test":
            return self._cep(req.url.path)
        if req.url.host == "brasilapi.test":
            return self._holidays(req.url.path)
        return httpx.Response(404)

    def _cep(self, path: str) -> httpx.Response:
        cep = path.removeprefix("/ws/").removesuffix("/json/")
        if cep == "01001000":
            return httpx.Response(200, json=SE)
        return httpx.Response(200, json={"erro": "true"})

    def _holidays(self, path: str) -> httpx.Response:
        if path == "/api/feriados/v1/2026":
            return httpx.Response(200, json=HOLIDAYS_2026)
        return httpx.Response(404, json={"message": "Ano fora do intervalo suportado."})
