"""Twelve Data: precio e historia diaria de EE.UU. (plan gratis: 8 consultas/min, 800/día).

El plan gratis no cubre la Bolsa de Santiago. El precio del plan gratis se trata como "con retraso".
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .base import Capabilities, ProviderError, Quote, RateLimited, SymbolNotFound, normalize_history
from .http_base import HttpProvider


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TwelveDataProvider(HttpProvider):
    name = "twelvedata"
    base_url = "https://api.twelvedata.com"
    capabilities = Capabilities(markets=frozenset({"US"}), realtime=frozenset(), history=True, needs_key=True)

    def _check(self, data, symbol: str) -> dict:
        if not isinstance(data, dict):
            raise ProviderError("twelvedata: formato inesperado")
        if data.get("status") == "error":
            code, msg = data.get("code"), data.get("message", "")
            if code == 429:
                raise RateLimited(f"twelvedata: {msg}")
            if code in (400, 404):
                raise SymbolNotFound(f"{symbol}: {msg}")
            raise ProviderError(f"twelvedata: {msg}")
        return data

    def get_quote(self, symbol: str) -> Quote:
        d = self._check(self._get("/quote", {"symbol": symbol, "apikey": self.api_key}), symbol)
        price = _num(d.get("close"))
        if price is None:
            raise SymbolNotFound(f"{symbol}: Twelve Data no tiene precio")
        ts = d.get("last_quote_at") or d.get("timestamp")
        as_of = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
        return Quote(
            symbol=symbol,
            price=price,
            prev_close=_num(d.get("previous_close")),
            currency=d.get("currency"),
            as_of=as_of,
            source=self.name,
            realtime=False,
            name=d.get("name"),
            exchange=d.get("exchange"),
            day_open=_num(d.get("open")),
            day_high=_num(d.get("high")),
            day_low=_num(d.get("low")),
            volume=_num(d.get("volume")),
        )

    def get_history(self, symbol, start=None, end=None) -> pd.DataFrame:
        params = {"symbol": symbol, "interval": "1day", "outputsize": 5000, "apikey": self.api_key, "order": "ASC"}
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d")
        d = self._check(self._get("/time_series", params), symbol)
        values = d.get("values") or []
        if not values:
            raise SymbolNotFound(f"{symbol}: Twelve Data sin historia")
        df = pd.DataFrame(values)
        df.index = pd.to_datetime(df.pop("datetime"))
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return normalize_history(df)
