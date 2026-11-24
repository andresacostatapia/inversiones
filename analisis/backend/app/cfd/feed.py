"""Velas intradía para los CFD: Twelve Data (si hay API key) con respaldo en Yahoo, y caché por temporalidad.

- Yahoo entrega futuros continuos (GC=F, ES=F…) casi 24 h, con ~10 min de retraso. Historia: 1 min ≈ 7 días,
  5 y 15 min 60 días, 1 hora 2 años. No tiene velas de 4 horas: se arman con las de 1 hora.
- Twelve Data (plan gratis) entrega oro y plata al contado (XAU/USD, XAG/USD) casi en tiempo real, pero solo
  8 consultas por minuto y 800 al día para toda la app: el copiloto usa su propio cupo menor y, si se agota,
  pasa a Yahoo. Como cada fuente cotiza distinto (contado vs futuro), la calibración guarda un desfase por fuente.

Todas las velas quedan con índice en UTC (hora de INICIO de la vela) y columnas open/high/low/close/volume.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from ..providers.base import ProviderError, RateLimited, RateLimiter, SymbolNotFound
from .assets import TF_DELTA, CfdAsset

log = logging.getLogger(__name__)

YAHOO = "yahoo"
TWELVEDATA = "twelvedata"
SOURCE_NAMES = {YAHOO: "Yahoo (futuro)", TWELVEDATA: "Twelve Data (contado)"}

YAHOO_PARAMS: dict[str, tuple[str, str]] = {  # temporalidad -> (intervalo, periodo)
    "1m": ("1m", "5d"), "5m": ("5m", "30d"), "15m": ("15m", "60d"), "1h": ("60m", "180d"), "1d": ("1d", "2y"),
}
TD_PARAMS: dict[str, tuple[str, int]] = {
    "1m": ("1min", 600), "5m": ("5min", 600), "15m": ("15min", 600), "1h": ("1h", 1200), "1d": ("1day", 600),
}
# Cuánto dura una consulta en caché (segundos), por temporalidad.
TTL: dict[str, int] = {"1m": 50, "5m": 110, "15m": 280, "1h": 590, "1d": 3500}
TD_MIN_TTL = 110  # Twelve Data gasta cupo diario: salvo 1 min, no se pide más de una vez cada ~2 min
MAX_BARS = 1500


class FeedError(ProviderError):
    """Ninguna fuente entregó velas para ese activo y temporalidad."""


@dataclass
class FeedBars:
    bars: pd.DataFrame
    source: str
    fetched_at: datetime
    error: str | None = None           # la última consulta falló y se muestran velas anteriores
    notes: list[str] = field(default_factory=list)


def _utc_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = df.copy()
    out.index = idx
    return out


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise SymbolNotFound("sin velas")
    out = df.rename(columns=lambda c: str(c).lower())
    for c in ("open", "high", "low", "close"):
        if c not in out.columns:
            raise ProviderError(f"falta la columna {c}")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out[["open", "high", "low", "close", "volume"]].astype(float)
    out = _utc_index(out)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out["close"] > 0)]
    if out.empty:
        raise SymbolNotFound("sin velas válidas")
    return out.iloc[-MAX_BARS:]


def yahoo_bars(symbol: str, tf: str) -> pd.DataFrame:
    """Velas de Yahoo (yfinance). Lanza RateLimited / SymbolNotFound / ProviderError."""
    import yfinance as yf

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    interval, period = YAHOO_PARAMS[tf]
    try:
        raw = yf.Ticker(symbol).history(interval=interval, period=period, auto_adjust=False, actions=False,
                                        raise_errors=True)
    except Exception as e:  # yfinance lanza varios tipos según el caso
        msg = str(e).lower()
        if "rate limit" in msg or "too many requests" in msg:
            raise RateLimited(f"Yahoo limitó las consultas: {e}") from e
        if any(k in msg for k in ("delisted", "no data", "not found", "no timezone", "404", "no price")):
            raise SymbolNotFound(f"{symbol}: Yahoo no tiene datos ({e})") from e
        raise ProviderError(f"{symbol}: error de Yahoo ({e})") from e
    return _clean(raw)


def twelvedata_bars(provider, symbol: str, tf: str) -> pd.DataFrame:
    """Velas de Twelve Data con hora en UTC. `provider` es un TwelveDataProvider (usa su cliente HTTP y su key)."""
    interval, size = TD_PARAMS[tf]
    data = provider._check(provider._get("/time_series", {
        "symbol": symbol, "interval": interval, "outputsize": size, "timezone": "UTC", "order": "ASC",
        "apikey": provider.api_key,
    }), symbol)
    values = data.get("values") or []
    if not values:
        raise SymbolNotFound(f"{symbol}: Twelve Data sin velas")
    df = pd.DataFrame(values)
    df.index = pd.to_datetime(df.pop("datetime"), utc=True)
    return _clean(df.apply(pd.to_numeric, errors="coerce"))


def resample_4h(bars_1h: pd.DataFrame) -> pd.DataFrame:
    """Velas de 4 horas desde las de 1 hora, alineadas a la sesión de CME (abre 17:00 hora de Chicago):
    17-21, 21-01, 01-05, 05-09, 09-13 y 13-16 (la última es más corta porque la sesión cierra a las 16:00)."""
    if bars_1h.empty:
        return bars_1h
    local = bars_1h.tz_convert("America/Chicago")
    out = local.resample("4h", origin="start_day", offset="1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "close"])
    return out.tz_convert("UTC")


class CfdFeed:
    """Entrega velas por activo y temporalidad, con caché, cupos propios y respaldo de Twelve Data a Yahoo."""

    def __init__(
        self,
        assets: dict[str, CfdAsset],
        td_provider=None,
        use_twelvedata: bool = True,
        td_timeframes: tuple[str, ...] = ("1m", "5m", "15m"),
        td_limiter: RateLimiter | None = None,
        yahoo_limiter: RateLimiter | None = None,
        yahoo_fetch: Callable[[str, str], pd.DataFrame] = yahoo_bars,
        td_fetch: Callable[..., pd.DataFrame] = twelvedata_bars,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.assets = assets
        self.td = td_provider
        self.use_twelvedata = use_twelvedata
        self.td_timeframes = set(td_timeframes)
        self.td_limiter = td_limiter or RateLimiter(6, 500)
        self.yahoo_limiter = yahoo_limiter or RateLimiter(40)
        self._yahoo_fetch = yahoo_fetch
        self._td_fetch = td_fetch
        self._clock = clock
        self._now = now
        self._cache: dict[tuple[str, str, str], tuple[float, FeedBars]] = {}
        self._locks: dict[tuple[str, str, str], threading.Lock] = {}
        self._guard = threading.Lock()
        self.last_td_error: str | None = None

    # --- fuentes -----------------------------------------------------------------
    def td_available(self, asset: CfdAsset) -> bool:
        return bool(self.use_twelvedata and asset.twelvedata and self.td is not None and self.td.is_configured())

    def sources_for(self, code: str, tf: str = "1m") -> list[str]:
        """Fuentes en orden de preferencia para un activo y temporalidad."""
        a = self.assets[code]
        return [TWELVEDATA, YAHOO] if self.td_available(a) and tf in self.td_timeframes else [YAHOO]

    def _lock(self, key) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _fetch(self, a: CfdAsset, tf: str, source: str) -> pd.DataFrame:
        if tf == "4h":
            return resample_4h(self.bars(a.code, "1h", source=source).bars)
        if source == TWELVEDATA:
            self.td_limiter.acquire()
            return self._td_fetch(self.td, a.twelvedata, tf)
        self.yahoo_limiter.acquire()
        return self._yahoo_fetch(a.yahoo, tf)

    def _ttl(self, tf: str, source: str) -> int:
        base = TTL.get(tf, TTL["1h"])
        return base if source == YAHOO or tf == "1m" else max(base, TD_MIN_TTL)

    def bars(self, code: str, tf: str, source: str | None = None) -> FeedBars:
        """Velas de `code` en `tf`. Sin `source`: la mejor fuente disponible, con respaldo en la siguiente."""
        if code not in self.assets:
            raise KeyError(code)
        if tf not in TF_DELTA:
            raise ValueError(f"temporalidad desconocida: {tf}")
        a = self.assets[code]
        order = [source] if source else self.sources_for(code, tf)
        errors: list[str] = []
        stale: FeedBars | None = None
        for src in order:
            key = (code, tf, src)
            with self._lock(key):
                hit = self._cache.get(key)
                ttl = self._ttl("1h" if tf == "4h" else tf, src)  # la de 4 h se rehace cuando vence la de 1 h
                if hit and self._clock() - hit[0] < ttl and hit[1].error is None:
                    return hit[1]
                try:
                    df = self._fetch(a, tf, src)
                    fb = FeedBars(df, src, self._now())
                    self._cache[key] = (self._clock(), fb)
                    if src == TWELVEDATA:
                        self.last_td_error = None
                    if errors:
                        fb = FeedBars(fb.bars, fb.source, fb.fetched_at, None, [f"Respaldo: {'; '.join(errors)}"])
                    return fb
                except Exception as e:  # red, cupo, símbolo: se intenta la fuente siguiente
                    msg = f"{SOURCE_NAMES.get(src, src)}: {e}"
                    log.info("cfd %s %s: %s", code, tf, msg)
                    errors.append(msg)
                    if src == TWELVEDATA:
                        self.last_td_error = str(e)
                    if hit and stale is None:  # velas anteriores de esta fuente, marcadas con el error
                        stale = FeedBars(hit[1].bars, src, hit[1].fetched_at, msg)
        if stale is not None:
            return stale
        raise FeedError("; ".join(errors) or "sin datos")

    def status(self) -> dict:
        return {
            "twelvedata_configured": bool(self.td is not None and self.td.is_configured() and self.use_twelvedata),
            "twelvedata_usage": self.td_limiter.usage(),
            "twelvedata_last_error": self.last_td_error,
            "yahoo_usage": self.yahoo_limiter.usage(),
        }
