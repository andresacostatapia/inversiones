from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import Capabilities, Quote, SymbolNotFound  # noqa: E402


def make_bars(n: int = 300, seed: int = 1, start: str = "2024-01-01", base: float = 100.0) -> pd.DataFrame:
    """Serie OHLCV sintética y reproducible (días hábiles)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, name="date")
    close = base * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    vol = rng.integers(1_000_000, 3_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


@pytest.fixture
def bars() -> pd.DataFrame:
    return make_bars()


class FakeProvider:
    """Proveedor en memoria para tests de router, servicio y API."""

    def __init__(self, name="fake", markets=("US", "CL", "FX"), realtime=(), history=True, configured=True,
                 known=("SPY", "VOO", "SQM-B.SN", "CHILE.SN", "CLP=X")):
        self.name = name
        self.capabilities = Capabilities(frozenset(markets), frozenset(realtime), history, needs_key=False)
        self.configured = configured
        self.known = set(known)
        self.fail_with: Exception | None = None
        self.calls: list[tuple[str, str]] = []
        self.price = {s: 100.0 + i for i, s in enumerate(sorted(self.known))}
        self.as_of = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)

    def is_configured(self):
        return self.configured

    def _check(self, symbol):
        if self.fail_with is not None:
            raise self.fail_with
        if symbol not in self.known:
            raise SymbolNotFound(f"{symbol}: no existe")

    def get_quote(self, symbol):
        self.calls.append(("quote", symbol))
        self._check(symbol)
        p = self.price[symbol]
        return Quote(symbol=symbol, price=p, prev_close=p / 1.01, currency="CLP" if symbol.endswith(".SN") else "USD",
                     as_of=self.as_of, source=self.name, realtime=bool(self.capabilities.realtime),
                     name=f"Nombre {symbol}", exchange="X", day_open=p, day_high=p * 1.01, day_low=p * 0.99,
                     volume=1000.0)

    def get_history(self, symbol, start=None, end=None):
        self.calls.append(("history", symbol))
        self._check(symbol)
        df = make_bars(300, seed=len(symbol), base=950.0 if symbol == "CLP=X" else 100.0)
        df.index = pd.bdate_range(end="2026-11-20", periods=len(df), name="date")  # termina la sesión anterior
        if start is not None:
            df = df[df.index >= pd.Timestamp(start).tz_localize(None).normalize() - timedelta(days=0)]
        return df


@pytest.fixture
def fake_provider():
    return FakeProvider()
