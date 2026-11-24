"""Backtest con datos sintéticos (sin red)."""
from __future__ import annotations

import numpy as np
import pytest
import pandas as pd

import backtest as bt
from config import ASSETS
from data_fetcher import resample_ohlcv
from tests.conftest import make_ohlc


def synthetic_history(drift: float = 0.0) -> dict:
    hist = {}
    for i, key in enumerate(ASSETS):
        h1 = make_ohlc(1500, freq="1h", seed=10 + i, drift=drift, vol=0.004)
        hist[key] = {"1h": h1, "4h": resample_ohlcv(h1, "4h")}
    return hist


def test_scores_have_no_lookahead() -> None:
    hist = synthetic_history()
    full = bt.compute_scores(hist)
    cut = {k: {tf: df[df.index < pd.Timestamp("2026-02-20", tz="UTC")] for tf, df in v.items()} for k, v in hist.items()}
    part = bt.compute_scores(cut)
    for k in ASSETS:
        common = part[k].index
        a, b = full[k].loc[common, "technical"], part[k]["technical"]
        assert np.allclose(a.fillna(0), b.fillna(0)), k  # Datos futuros no cambian el pasado


def test_simulation_runs_and_r_values_are_consistent() -> None:
    scores = bt.compute_scores(synthetic_history(drift=0.0004))
    res = bt.simulate(scores, bt.BacktestParams(threshold=40))
    assert res.summary["operaciones"] > 0
    sl = res.trades[res.trades["motivo"] == "SL"]["R"]
    tp = res.trades[res.trades["motivo"] == "TP"]["R"]
    assert np.allclose(sl, -1.0) and np.allclose(tp, 2.0)
    assert res.equity.iloc[-1] == pytest.approx(res.equity.iloc[0] + res.trades["P/L"].sum())


def test_limits_respected() -> None:
    scores = bt.compute_scores(synthetic_history(drift=0.0004))
    res = bt.simulate(scores, bt.BacktestParams(threshold=30, max_open_positions=2))
    t = res.trades
    for ts in t["entrada_hora"].unique():
        open_now = ((t["entrada_hora"] <= ts) & (t["salida_hora"] > ts)).sum()
        assert open_now <= 2


def test_verdict_small_sample() -> None:
    level, _ = bt.verdict({"operaciones": 5, "profit_factor": 3, "expectativa_R": 1})
    assert level == "warn"
