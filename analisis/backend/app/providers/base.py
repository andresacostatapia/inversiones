"""Contrato común de los proveedores de datos de mercado."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import pandas as pd

# Columnas que todo proveedor debe entregar en get_history (índice: fecha, sin zona horaria).
OHLCV = ["open", "high", "low", "close", "volume"]


class ProviderError(Exception):
    """Fallo genérico del proveedor (red, formato inesperado...)."""


class SymbolNotFound(ProviderError):
    """El proveedor no conoce el símbolo o no tiene datos para él."""


class RateLimited(ProviderError):
    """Se alcanzó el límite de consultas (propio o informado por el proveedor)."""


class NotSupported(ProviderError):
    """El proveedor no ofrece esa operación o ese mercado (p. ej. historia en el plan gratis)."""


class MissingApiKey(NotSupported):
    pass


def market_of(symbol: str) -> str:
    """Mercado según el símbolo: CL (Bolsa de Santiago, sufijo .SN), FX (divisas) o US."""
    s = symbol.upper()
    if s.endswith(".SN"):
        return "CL"
    if s.endswith("=X") or s == "USDCLP":
        return "FX"
    return "US"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Capabilities:
    markets: frozenset[str]          # mercados que cubre: {"US", "CL", "FX"}
    realtime: frozenset[str]         # mercados con dato en tiempo real (el resto: con retraso)
    history: bool                    # si entrega historia diaria
    needs_key: bool


@dataclass
class Quote:
    symbol: str
    price: float
    prev_close: float | None
    currency: str | None
    as_of: datetime                  # hora del último precio según el proveedor (UTC)
    source: str
    realtime: bool                   # el proveedor declara el dato como tiempo real
    fetched_at: datetime = field(default_factory=utcnow)
    name: str | None = None
    exchange: str | None = None
    day_high: float | None = None
    day_low: float | None = None
    day_open: float | None = None
    volume: float | None = None

    @property
    def change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return (self.price / self.prev_close - 1) * 100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["change_pct"] = self.change_pct
        d["as_of"] = self.as_of.isoformat()
        d["fetched_at"] = self.fetched_at.isoformat()
        return d


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    capabilities: Capabilities

    def is_configured(self) -> bool: ...

    def get_quote(self, symbol: str) -> Quote: ...

    def get_history(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """Velas diarias con columnas OHLCV e índice DatetimeIndex (fecha de la sesión, sin zona)."""
        ...


class RateLimiter:
    """Ventana deslizante por minuto y por día. No espera: si no hay cupo, lanza RateLimited."""

    def __init__(self, per_minute: int | None = None, per_day: int | None = None, clock=time.monotonic):
        self.per_minute = per_minute
        self.per_day = per_day
        self._clock = clock
        self._minute: deque[float] = deque()
        self._day: deque[float] = deque()
        self._lock = threading.Lock()

    @staticmethod
    def _trim(q: deque, now: float, window: float) -> None:
        while q and now - q[0] >= window:
            q.popleft()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            self._trim(self._minute, now, 60)
            self._trim(self._day, now, 86400)
            if self.per_minute is not None and len(self._minute) >= self.per_minute:
                raise RateLimited(f"límite propio de {self.per_minute} consultas/min")
            if self.per_day is not None and len(self._day) >= self.per_day:
                raise RateLimited(f"límite propio de {self.per_day} consultas/día")
            self._minute.append(now)
            self._day.append(now)

    def usage(self) -> dict:
        with self._lock:
            now = self._clock()
            self._trim(self._minute, now, 60)
            self._trim(self._day, now, 86400)
            return {
                "last_minute": len(self._minute),
                "per_minute": self.per_minute,
                "last_day": len(self._day),
                "per_day": self.per_day,
            }


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    """Deja la historia en formato común: OHLCV en minúscula, índice de fechas sin zona, ordenado y sin duplicados."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], name="date"))
    out = df.rename(columns=lambda c: str(c).lower().replace(" ", "_"))
    missing = [c for c in OHLCV if c not in out.columns]
    if missing:
        raise ProviderError(f"faltan columnas {missing}")
    # adj_close (ajustado por dividendos y splits) es opcional: lo usa el backtest para el retorno total.
    cols = OHLCV + (["adj_close"] if "adj_close" in out.columns else [])
    out = out[cols].astype(float)
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])
