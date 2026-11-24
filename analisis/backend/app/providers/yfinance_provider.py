"""yfinance (Yahoo Finance): gratis, sin API key, cubre EE.UU., Santiago (.SN) y divisas (=X).

Yahoo no garantiza tiempo real: el dato puede venir con retraso (en Santiago suele ser mayor),
así que se declara siempre como "con retraso" y la UI muestra la hora exacta del último precio.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from .base import (
    Capabilities,
    ProviderError,
    Quote,
    RateLimited,
    SymbolNotFound,
    drop_placeholder_bars,
    market_of,
    normalize_history,
)

try:  # las excepciones cambian de nombre entre versiones de yfinance
    from yfinance.exceptions import YFRateLimitError
except Exception:  # pragma: no cover
    class YFRateLimitError(Exception):
        pass


def _ticker(symbol: str):
    import yfinance as yf

    # Los errores se informan con nuestras excepciones; el log propio de yfinance solo mete ruido.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    return yf.Ticker(symbol)


class YFinanceProvider:
    name = "yfinance"
    capabilities = Capabilities(
        markets=frozenset({"US", "CL", "FX", "GLB", "CRY", "OTHER"}),
        realtime=frozenset(),
        history=True,
        needs_key=False,
    )

    def __init__(self, ticker_factory=_ticker):
        self._ticker = ticker_factory

    def is_configured(self) -> bool:
        return True

    def _history(self, symbol: str, **kwargs) -> tuple[pd.DataFrame, dict]:
        t = self._ticker(symbol)
        try:
            raw = t.history(interval="1d", auto_adjust=False, actions=False, raise_errors=True, **kwargs)
        except YFRateLimitError as e:
            raise RateLimited(f"Yahoo limitó las consultas: {e}") from e
        except Exception as e:  # yfinance lanza varios tipos según el caso
            msg = str(e).lower()
            if "rate limit" in msg or "too many requests" in msg:
                raise RateLimited(f"Yahoo limitó las consultas: {e}") from e
            if any(k in msg for k in ("delisted", "no data", "not found", "no timezone", "404", "no price")):
                raise SymbolNotFound(f"{symbol}: Yahoo no tiene datos ({e})") from e
            raise ProviderError(f"{symbol}: error de Yahoo ({e})") from e
        if raw is None or raw.empty:
            raise SymbolNotFound(f"{symbol}: Yahoo no tiene datos")
        meta = getattr(t, "history_metadata", None) or {}
        return raw, meta

    def get_quote(self, symbol: str) -> Quote:
        raw, meta = self._history(symbol, period="5d")
        bars = normalize_history(raw)
        if bars.empty:
            raise SymbolNotFound(f"{symbol}: sin precios recientes")
        last = bars.iloc[-1]
        price = meta.get("regularMarketPrice")
        price = float(price) if isinstance(price, (int, float)) else float(last["close"])

        mtime = meta.get("regularMarketTime")
        if isinstance(mtime, (int, float)):
            as_of = datetime.fromtimestamp(mtime, tz=timezone.utc)
        elif isinstance(mtime, (pd.Timestamp, datetime)):
            as_of = pd.Timestamp(mtime).tz_convert("UTC").to_pydatetime()
        else:
            ts = pd.Timestamp(raw.index[-1])
            as_of = (ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")).to_pydatetime()

        # "Cierre anterior" = la sesión previa a la del último precio. Se toma de la propia serie,
        # porque chartPreviousClose de Yahoo es el cierre previo al RANGO pedido, no al día.
        tz = meta.get("exchangeTimezoneName") or "UTC"
        session_day = pd.Timestamp(as_of).tz_convert(tz).normalize().tz_localize(None)
        earlier = bars[bars.index < session_day]
        prev_close = float(earlier["close"].iloc[-1]) if len(earlier) else None
        today = bars.iloc[-1] if bars.index[-1] == session_day else None

        return Quote(
            symbol=symbol,
            price=price,
            prev_close=prev_close,
            currency=meta.get("currency"),
            as_of=as_of,
            source=self.name,
            realtime=False,
            name=meta.get("longName") or meta.get("shortName"),
            exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
            day_open=None if today is None else float(today["open"]),
            day_high=None if today is None else float(today["high"]),
            day_low=None if today is None else float(today["low"]),
            volume=None if today is None else float(today["volume"]),
            kind=meta.get("instrumentType"),
            tz=meta.get("exchangeTimezoneName"),
        )

    def get_history(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        if start is None:
            raw, _ = self._history(symbol, period="max")
        else:
            raw, _ = self._history(symbol, start=start, end=end)
        bars = normalize_history(raw)
        return bars if market_of(symbol) == "FX" else drop_placeholder_bars(bars)



TYPE_LABELS = {"EQUITY": "Acción", "ETF": "ETF", "INDEX": "Índice", "FUTURE": "Futuro", "CURRENCY": "Divisa",
               "CRYPTOCURRENCY": "Cripto", "MUTUALFUND": "Fondo", "OPTION": "Opción"}


def search(query: str, limit: int = 10) -> list[dict]:
    """Búsqueda de símbolos por nombre o código (Yahoo). Devuelve símbolo, nombre, bolsa y tipo."""
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        quotes = yf.Search(query, max_results=limit, news_count=0, lists_count=0).quotes or []
    except Exception as e:
        msg = str(e).lower()
        if "rate limit" in msg or "too many requests" in msg:
            raise RateLimited(f"Yahoo limitó las consultas: {e}") from e
        raise ProviderError(f"búsqueda en Yahoo falló ({e})") from e
    out = []
    for q in quotes:
        sym = q.get("symbol")
        kind = q.get("quoteType")
        if not sym or kind == "OPTION":
            continue
        out.append({"symbol": sym, "name": q.get("longname") or q.get("shortname") or sym,
                    "exchange": q.get("exchDisp") or q.get("exchange"), "kind": kind,
                    "kind_label": TYPE_LABELS.get(kind or "", kind or "")})
    return out
