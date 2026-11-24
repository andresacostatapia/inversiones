"""Elige proveedor por mercado, respeta límites de consulta y pasa al siguiente si uno falla."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ..config import AppConfig, Secrets
from .alphavantage import AlphaVantageProvider
from .base import MarketDataProvider, NotSupported, ProviderError, Quote, RateLimited, RateLimiter, SymbolNotFound, market_of, utcnow
from .finnhub import FinnhubProvider
from .twelvedata import TwelveDataProvider
from .yfinance_provider import YFinanceProvider

# Tras varias fallas seguidas, un proveedor descansa un rato antes de reintentarse.
FAILS_BEFORE_COOLDOWN = 3
COOLDOWN_BASE_S = 60
COOLDOWN_MAX_S = 1800


@dataclass
class ProviderHealth:
    last_ok: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # time.monotonic()

    def to_dict(self, now_mono: float) -> dict:
        return {
            "last_ok": self.last_ok.isoformat() if self.last_ok else None,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "consecutive_failures": self.consecutive_failures,
            "cooling_down_s": max(0, round(self.cooldown_until - now_mono)),
        }


class AllProvidersFailed(ProviderError):
    pass


class ProviderRouter:
    def __init__(
        self,
        providers: dict[str, MarketDataProvider],
        quote_order: dict[str, list[str]],
        history_order: dict[str, list[str]],
        limiters: dict[str, RateLimiter] | None = None,
        clock=time.monotonic,
    ):
        self.providers = providers
        self.quote_order = quote_order
        self.history_order = history_order
        self.limiters = limiters or {}
        self.health = {n: ProviderHealth() for n in providers}
        self._clock = clock
        self._lock = threading.Lock()

    # --- selección ---------------------------------------------------------
    def _candidates(self, symbol: str, order: dict[str, list[str]], need_history: bool) -> list[MarketDataProvider]:
        market = market_of(symbol)
        names = order.get(market) or ["yfinance"]
        out = []
        for n in names:
            p = self.providers.get(n)
            if p is None or not p.is_configured() or market not in p.capabilities.markets:
                continue
            if need_history and not p.capabilities.history:
                continue
            out.append(p)
        return out

    def _available(self, p: MarketDataProvider) -> bool:
        return self.health[p.name].cooldown_until <= self._clock()

    # --- registro de salud -------------------------------------------------
    def _ok(self, name: str) -> None:
        with self._lock:
            h = self.health[name]
            h.last_ok, h.consecutive_failures, h.cooldown_until = utcnow(), 0, 0.0

    def _fail(self, name: str, err: Exception) -> None:
        with self._lock:
            h = self.health[name]
            h.last_error, h.last_error_at = str(err), utcnow()
            h.consecutive_failures += 1
            if isinstance(err, RateLimited) or h.consecutive_failures >= FAILS_BEFORE_COOLDOWN:
                n = max(1, h.consecutive_failures - FAILS_BEFORE_COOLDOWN + 1)
                h.cooldown_until = self._clock() + min(COOLDOWN_MAX_S, COOLDOWN_BASE_S * 2 ** (n - 1))

    # --- llamadas ----------------------------------------------------------
    def _call(self, symbol: str, order, need_history: bool, fn):
        candidates = self._candidates(symbol, order, need_history)
        if not candidates:
            raise AllProvidersFailed(f"{symbol}: ningún proveedor configurado cubre el mercado {market_of(symbol)}")
        errors, not_found, real_failures = [], 0, 0
        for p in candidates:
            if not self._available(p):
                errors.append(f"{p.name}: en pausa por fallas recientes")
                continue
            try:
                if p.name in self.limiters:
                    self.limiters[p.name].acquire()
                result = fn(p)
                self._ok(p.name)
                return result
            except SymbolNotFound as e:
                # Un símbolo inexistente no es culpa del proveedor: no cuenta como falla.
                not_found += 1
                errors.append(f"{p.name}: {e}")
            except NotSupported as e:
                errors.append(f"{p.name}: {e}")
            except RateLimited as e:
                real_failures += 1
                if "límite propio" not in str(e):
                    self._fail(p.name, e)
                errors.append(f"{p.name}: {e}")
            except Exception as e:
                real_failures += 1
                self._fail(p.name, e)
                errors.append(f"{p.name}: {e}")
        # Solo se afirma "no existe" si ningún proveedor falló por otra razón (red, límites...).
        if not_found and not real_failures:
            raise SymbolNotFound(f"{symbol}: sin datos en ningún proveedor")
        raise AllProvidersFailed("; ".join(errors))

    def get_quote(self, symbol: str) -> Quote:
        return self._call(symbol, self.quote_order, False, lambda p: p.get_quote(symbol))

    def get_history(self, symbol: str, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        def fetch(p):
            df = p.get_history(symbol, start, end)
            df.attrs["source"] = p.name  # para saber de dónde vino cada vela
            return df

        return self._call(symbol, self.history_order, True, fetch)

    def status(self) -> list[dict]:
        now = self._clock()
        out = []
        for name, p in self.providers.items():
            out.append(
                {
                    "name": name,
                    "configured": p.is_configured(),
                    "markets": sorted(p.capabilities.markets),
                    "realtime_markets": sorted(p.capabilities.realtime),
                    "history": p.capabilities.history,
                    "usage": self.limiters[name].usage() if name in self.limiters else None,
                    **self.health[name].to_dict(now),
                }
            )
        return out


def build_router(cfg: AppConfig, secrets: Secrets) -> ProviderRouter:
    providers: dict[str, MarketDataProvider] = {
        "yfinance": YFinanceProvider(),
        "finnhub": FinnhubProvider(secrets.finnhub_api_key),
        "twelvedata": TwelveDataProvider(secrets.twelvedata_api_key),
        "alphavantage": AlphaVantageProvider(secrets.alphavantage_api_key),
    }
    limiters = {
        n: RateLimiter(rl.per_minute, rl.per_day) for n, rl in cfg.data.rate_limits.items() if n in providers
    }
    return ProviderRouter(providers, cfg.data.providers.quotes, cfg.data.providers.history, limiters)
