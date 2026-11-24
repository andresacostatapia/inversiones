"""Finnhub: precio actual de EE.UU. en tiempo real con plan gratis (60 consultas/min).

La historia diaria (/stock/candle) es de pago desde 2024, así que aquí no se ofrece:
el router usa otro proveedor para la historia. Tampoco cubre la Bolsa de Santiago en el plan gratis.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import Capabilities, NotSupported, ProviderError, Quote, SymbolNotFound
from .http_base import HttpProvider


class FinnhubProvider(HttpProvider):
    name = "finnhub"
    base_url = "https://finnhub.io/api/v1"
    capabilities = Capabilities(markets=frozenset({"US"}), realtime=frozenset({"US"}), history=False, needs_key=True)

    def get_quote(self, symbol: str) -> Quote:
        data = self._get("/quote", {"symbol": symbol, "token": self.api_key})
        if not isinstance(data, dict):
            raise ProviderError("finnhub: formato inesperado")
        if data.get("error"):
            raise ProviderError(f"finnhub: {data['error']}")
        price, ts = data.get("c"), data.get("t")
        # Finnhub responde 200 con todo en 0 cuando el símbolo no existe.
        if not price or not ts:
            raise SymbolNotFound(f"{symbol}: Finnhub no tiene datos")
        return Quote(
            symbol=symbol,
            price=float(price),
            prev_close=float(data["pc"]) if data.get("pc") else None,
            currency="USD",
            as_of=datetime.fromtimestamp(ts, tz=timezone.utc),
            source=self.name,
            realtime=True,
            day_open=data.get("o"),
            day_high=data.get("h"),
            day_low=data.get("l"),
        )

    def get_history(self, symbol, start=None, end=None):
        raise NotSupported("finnhub: la historia diaria requiere plan pagado")
