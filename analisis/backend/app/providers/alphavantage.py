"""Alpha Vantage: precio e historia diaria de EE.UU. (plan gratis: 25 consultas/DÍA).

Con tan pocas consultas sirve como respaldo de historia, no para el polling de cada minuto.
En el plan gratis TIME_SERIES_DAILY entrega solo los últimos 100 días ("compact").
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .base import Capabilities, ProviderError, Quote, RateLimited, SymbolNotFound, normalize_history
from .http_base import HttpProvider

NY = ZoneInfo("America/New_York")


class AlphaVantageProvider(HttpProvider):
    name = "alphavantage"
    base_url = "https://www.alphavantage.co"
    capabilities = Capabilities(markets=frozenset({"US"}), realtime=frozenset(), history=True, needs_key=True)

    def _query(self, symbol: str, **params) -> dict:
        data = self._get("/query", {**params, "symbol": symbol, "apikey": self.api_key})
        if not isinstance(data, dict):
            raise ProviderError("alphavantage: formato inesperado")
        # Alpha Vantage responde 200 con un mensaje cuando se excede el límite o falla algo.
        note = data.get("Note") or data.get("Information")
        if note:
            if "rate limit" in note.lower() or "frequency" in note.lower() or "requests per day" in note.lower():
                raise RateLimited(f"alphavantage: {note}")
            raise ProviderError(f"alphavantage: {note}")
        if "Error Message" in data:
            raise SymbolNotFound(f"{symbol}: {data['Error Message']}")
        return data

    def get_quote(self, symbol: str) -> Quote:
        q = self._query(symbol, function="GLOBAL_QUOTE").get("Global Quote") or {}
        if not q.get("05. price"):
            raise SymbolNotFound(f"{symbol}: Alpha Vantage no tiene precio")
        day = datetime.strptime(q["07. latest trading day"], "%Y-%m-%d").date()
        # Solo informa el día; se asume el cierre de Nueva York de ese día como hora del dato.
        as_of = datetime.combine(day, time(16, 0), tzinfo=NY).astimezone(timezone.utc)
        return Quote(
            symbol=symbol,
            price=float(q["05. price"]),
            prev_close=float(q["08. previous close"]) if q.get("08. previous close") else None,
            currency="USD",
            as_of=as_of,
            source=self.name,
            realtime=False,
            day_open=float(q["02. open"]),
            day_high=float(q["03. high"]),
            day_low=float(q["04. low"]),
            volume=float(q["06. volume"]),
        )

    def get_history(self, symbol, start=None, end=None) -> pd.DataFrame:
        series = self._query(symbol, function="TIME_SERIES_DAILY", outputsize="compact").get("Time Series (Daily)")
        if not series:
            raise SymbolNotFound(f"{symbol}: Alpha Vantage sin historia")
        df = pd.DataFrame.from_dict(series, orient="index")
        df.columns = [c.split(". ", 1)[-1] for c in df.columns]
        df.index = pd.to_datetime(df.index)
        df = normalize_history(df)
        if start is not None:
            df = df[df.index >= pd.Timestamp(start).tz_localize(None).normalize()]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end).tz_localize(None).normalize()]
        return df
