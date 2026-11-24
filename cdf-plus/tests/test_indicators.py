"""El score vectorizado (backtest) debe coincidir exactamente con el de la app/bot."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import indicators as ind
from tests.conftest import make_ohlc


@pytest.mark.parametrize("seed,drift", [(1, 0.0), (2, 0.0004), (3, -0.0004), (4, 0.0)])
def test_vectorized_score_matches_scalar(seed: int, drift: float) -> None:
    df = make_ohlc(600, seed=seed, drift=drift)
    vec = ind.tf_score_series(ind.add_indicators(df))
    for end in range(250, 600, 37):
        scalar = ind.analyze_timeframe(df.iloc[:end], "1h")
        assert scalar.ok
        assert vec.iloc[end - 1] == pytest.approx(scalar.score, abs=1e-9), f"vela {end}"


def test_technical_vec_matches_scalar() -> None:
    frames = {tf: make_ohlc(400, seed=s) for tf, s in (("15m", 5), ("1h", 6), ("4h", 7))}
    tfs = {tf: ind.analyze_timeframe(df, tf) for tf, df in frames.items()}
    scalar, _ = ind.technical_score(tfs)
    vec = ind.technical_score_vec(pd.Series([tfs["15m"].score]), pd.Series([tfs["1h"].score]),
                                  pd.Series([tfs["4h"].score]))
    assert vec.iloc[0] == pytest.approx(scalar, abs=1e-9)


def test_flat_series_is_neutral() -> None:
    idx = pd.date_range("2026-01-01", periods=300, freq="15min", tz="UTC")
    flat = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)
    res = ind.analyze_timeframe(flat, "15m")
    assert res.score == 0 and res.bias == "LATERAL"
    assert ind.tf_score_series(ind.add_indicators(flat)).abs().max() == 0


def test_rsi_bounds_and_atr_positive() -> None:
    df = make_ohlc(500, seed=9)
    data = ind.add_indicators(df)
    r = data["rsi"].dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert (data["atr"].dropna() > 0).all()


def test_intermarket_regimes() -> None:
    on = ind.intermarket_from_trends({"US500": 60, "US100": 70, "US30": 50, "US2000": 55, "GOLD": -40, "SILVER": -50})
    assert on.regime == "RISK_ON" and on.breadth_state == "SOLIDA"
    off = ind.intermarket_from_trends({"US500": -60, "US100": -70, "US30": -50, "US2000": -55, "GOLD": 40, "SILVER": 50})
    assert off.regime == "RISK_OFF"
    m_buy_idx, _ = ind.intermarket_multiplier("US500", 1, off)
    m_buy_gold, _ = ind.intermarket_multiplier("GOLD", 1, off)
    assert m_buy_idx < 1.0 < m_buy_gold
    none = ind.intermarket_from_trends({"GOLD": 10})
    assert not none.available
    assert ind.intermarket_multiplier("GOLD", 1, none)[0] == 1.0
