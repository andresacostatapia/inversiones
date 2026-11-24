import numpy as np
import pandas as pd
import pytest

from app.backtest import adjusted, buy_and_hold, metrics, run_backtest, simulate
from app.config import BacktestConfig, SignalsConfig

from .conftest import make_bars

IDX = pd.bdate_range("2025-01-01", periods=8)


def series(vals):
    return pd.Series(vals, index=IDX[: len(vals)], dtype=float)


def test_executes_next_open_without_lookahead():
    open_ = series([10, 10, 11, 12, 13, 14, 15, 16])
    close = series([10, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5])
    score = series([0, 50, 0, 0, -50, 0, 0, 0])  # compra al cierre del día 1, vende al cierre del día 4
    sim = simulate(open_, close, score, buy=30, sell=-30, cost=0.0, start=0)
    t = sim.trades[0]
    assert t.entry_date == IDX[2].date().isoformat() and t.entry_price == 11   # apertura del día 2
    assert t.exit_date == IDX[5].date().isoformat() and t.exit_price == 14     # apertura del día 5
    assert t.return_pct == pytest.approx((14 / 11 - 1) * 100, abs=0.01)
    assert sim.equity.iloc[-1] == pytest.approx(14 / 11)
    assert sim.in_market.tolist() == [0, 0, 1, 1, 1, 0, 0, 0]


def test_hold_zone_keeps_position():
    open_ = close = series([10, 10, 10, 10, 10, 10])
    score = series([50, 10, -10, 20, -29, 0])  # entre umbrales: mantiene lo que tenga
    sim = simulate(open_, close, score, buy=30, sell=-30, cost=0, start=0)
    assert sim.in_market.tolist() == [0, 1, 1, 1, 1, 1] and sim.trades[-1].open


def test_costs_are_charged_on_both_sides():
    open_ = close = series([100, 100, 100, 100, 100])
    score = series([50, 0, -50, 0, 0])
    sim = simulate(open_, close, score, buy=30, sell=-30, cost=0.01, start=0)
    assert sim.equity.iloc[-1] == pytest.approx(0.99 * 0.99)
    assert sim.trades[0].return_pct == pytest.approx((0.99 * 0.99 - 1) * 100, abs=0.01)


def test_nan_score_means_no_action():
    open_ = close = series([10, 11, 12, 13])
    sim = simulate(open_, close, series([np.nan] * 4), 30, -30, 0, 0)
    assert sim.equity.iloc[-1] == 1 and not sim.trades


def test_buy_and_hold_and_metrics():
    open_ = series([100, 100, 120, 90, 110])
    close = series([100, 110, 120, 90, 110])
    bh = buy_and_hold(open_, close, cost=0.0, start=0)
    m = metrics(bh)
    assert m["total_return_pct"] == pytest.approx(10.0)
    assert m["max_drawdown_pct"] == pytest.approx(-25.0)  # 120 → 90
    assert m["exposure_pct"] == 100 and m["open_position"] and m["trades"] == 0 and m["win_rate_pct"] is None


def test_win_rate():
    open_ = close = series([10, 10, 12, 12, 9, 9, 9, 9])
    score = series([50, 0, -50, 50, 0, -50, 0, 0])  # gana (10→12), pierde (12→9)
    m = metrics(simulate(open_, close, score, 30, -30, 0, 0))
    assert m["trades"] == 2 and m["win_rate_pct"] == 50.0


def test_adjusted_prices():
    bars = pd.DataFrame({"open": [10, 10], "high": [11, 11], "low": [9, 9], "close": [10, 10], "volume": [1, 1],
                         "adj_close": [9, 10]}, index=IDX[:2])
    a = adjusted(bars)
    assert a["open"].tolist() == [9, 10] and a["close"].tolist() == [9, 10]


def test_run_backtest_end_to_end():
    bars = make_bars(900, seed=21)
    r = run_backtest("X", bars, SignalsConfig(), BacktestConfig(commission_pct=0.5), years=2)
    assert 1.9 <= r["years"] <= 2.1
    keys = [s["key"] for s in r["strategies"]]
    assert keys == ["trend", "rsi", "macd", "bollinger", "composite"]
    for s in r["strategies"]:
        m = s["metrics"]
        assert m["max_drawdown_pct"] <= 0 and 0 <= m["exposure_pct"] <= 100
        assert s["vs_buy_hold"]["verdict"]
        assert s["equity"][0]["value"] == pytest.approx(1.0, abs=0.05)
    assert r["buy_hold"]["metrics"]["exposure_pct"] == 100
    assert "sobreajuste" in r["warning"]


def test_run_backtest_rejects_short_history():
    with pytest.raises(ValueError, match="insuficiente"):
        run_backtest("X", make_bars(150), SignalsConfig(), BacktestConfig())


def test_higher_commission_never_helps():
    bars = make_bars(900, seed=4)
    lo = run_backtest("X", bars, SignalsConfig(), BacktestConfig(commission_pct=0.0), include_curves=False)
    hi = run_backtest("X", bars, SignalsConfig(), BacktestConfig(commission_pct=1.0), include_curves=False)
    for a, b in zip(lo["strategies"], hi["strategies"]):
        assert b["metrics"]["total_return_pct"] <= a["metrics"]["total_return_pct"] + 1e-9
