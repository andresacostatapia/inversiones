import numpy as np
import pandas as pd
import pytest

from app import indicators as ind
from app.strategies import STRATEGIES
from app.strategies.base import bars_since, crossed

from .conftest import make_bars


def frame(n=10, **cols):
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({k: (v if hasattr(v, "__len__") else [v] * n) for k, v in cols.items()}, index=idx, dtype=float)


def trending(n=400, drift=0.001, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n)))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996, "close": close,
                         "volume": 1e6}, index=idx)


def test_bars_since_and_crossed():
    ev = pd.Series([False, True, False, False, True, False])
    assert bars_since(ev).tolist()[1:] == [0, 1, 2, 0, 1] and np.isnan(bars_since(ev).iloc[0])
    flag = pd.Series([np.nan, True, True, False, False, True], dtype=object)
    assert crossed(flag).tolist() == [False, False, False, True, False, True]


def test_all_scores_bounded_on_random_data():
    df = ind.compute_all(make_bars(600, seed=7))
    for key, strat in STRATEGIES.items():
        sc = strat.compute(df)["score"].dropna()
        assert len(sc) > 300, key
        assert sc.between(-100, 100).all(), key


def test_trend_up_and_down():
    t = STRATEGIES["trend"]
    up = t.compute(ind.compute_all(trending(drift=0.002)))["score"].iloc[-1]
    down = t.compute(ind.compute_all(trending(drift=-0.002)))["score"].iloc[-1]
    assert up > 80 and down < -80
    # Sin 200 días de historia no opina.
    assert t.compute(ind.compute_all(trending(150)))["score"].isna().all()


def test_trend_components_and_reasons():
    df = frame(10, close=105, sma50=102, sma200=100, ema20=np.linspace(100, 104.5, 10))
    out = STRATEGIES["trend"].compute(df)
    # distancia +5 % → +35; cruce dorado → +35; EMA20 sube 2,5 % en 5 días (satura) → +30
    assert out["score"].iloc[-1] == pytest.approx(100)
    reasons = STRATEGIES["trend"].explain(pd.concat([df.iloc[-1], out.iloc[-1]]))
    assert reasons[0].startswith("Precio 5,0 % sobre la SMA200")
    assert "SMA50 sobre la SMA200" in reasons[1]


@pytest.mark.parametrize("rsi,expected", [(15, 100), (20, 100), (28, 68), (30, 60), (50, 0), (65, -40), (85, -100)])
def test_rsi_mapping(rsi, expected):
    out = STRATEGIES["rsi"].compute(frame(3, rsi14=rsi))
    assert out["score"].iloc[-1] == pytest.approx(expected)


def test_rsi_reasons():
    df = frame(3, rsi14=[25, 28, 33])
    row = pd.concat([df.iloc[-1], STRATEGIES["rsi"].compute(df).iloc[-1]])
    assert STRATEGIES["rsi"].explain(row) == ["RSI 33: cerca de sobreventa", "RSI salió de sobreventa hoy"]


def test_macd_fresh_cross_weighs_more():
    # MACD cruza sobre la señal en el día 3 y sigue arriba.
    df = frame(10, macd=[-1, -0.5, 0.2, 0.5, 0.8, 1, 1, 1, 1, 1], macd_signal=0.1)
    s = STRATEGIES["macd"].compute(df)["score"]
    assert s.iloc[1] == -60          # bajo la señal y bajo cero
    assert s.iloc[2] == 100          # cruce fresco (+40 +40) y sobre cero (+20)
    assert s.iloc[5] == 100          # 3 días después sigue siendo "fresco"
    assert s.iloc[6] == 60           # ya no
    row = pd.concat([df.iloc[2], STRATEGIES["macd"].compute(df).iloc[2]])
    assert STRATEGIES["macd"].explain(row)[0] == "Cruce MACD alcista hoy"


def test_bollinger_with_trend_filter():
    b = STRATEGIES["bollinger"]
    up = frame(3, bb_pctb=0.0, close=110, sma200=100)
    down = frame(3, bb_pctb=0.0, close=90, sma200=100)
    assert b.compute(up)["score"].iloc[-1] == 80
    assert b.compute(down)["score"].iloc[-1] == 40      # comprar la caída contra la tendencia: la mitad
    top_up = frame(3, bb_pctb=1.0, close=110, sma200=100)
    assert b.compute(top_up)["score"].iloc[-1] == -40   # vender una subida en tendencia alcista: la mitad
    assert b.compute(frame(3, bb_pctb=-1.0, close=110, sma200=np.nan))["score"].iloc[-1] == 100


def test_strategies_have_no_lookahead():
    bars = make_bars(500, seed=11)
    full = ind.compute_all(bars)
    part = ind.compute_all(bars.iloc[:400])
    for key, strat in STRATEGIES.items():
        a = strat.compute(full)["score"].iloc[:400]
        b = strat.compute(part)["score"]
        pd.testing.assert_series_equal(a, b, check_names=False, obj=key)
