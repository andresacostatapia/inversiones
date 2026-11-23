import numpy as np
import pandas as pd
import pytest

from app import indicators as ind

# Datos de ejemplo de StockCharts (artículo "Relative Strength Index"), con sus valores de RSI(14) publicados.
SC_CLOSE = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
    46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521,
    45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314,
]
SC_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42,
          39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def s(values):
    return pd.Series(values, dtype=float, index=pd.bdate_range("2024-01-01", periods=len(values)))


def test_rsi_matches_stockcharts_reference():
    r = ind.rsi(s(SC_CLOSE), 14)
    assert r.iloc[:14].isna().all()
    np.testing.assert_allclose(r.iloc[14:].to_numpy(), SC_RSI, atol=0.01)


def test_rsi_extremes():
    assert ind.rsi(s(range(1, 40)), 14).iloc[-1] == 100.0  # solo subidas
    assert ind.rsi(s(range(40, 1, -1)), 14).iloc[-1] == pytest.approx(0.0)  # solo bajadas
    assert ind.rsi(s([10.0] * 30), 14).iloc[-1] == 50.0  # plano


def test_sma_and_seeded_ema():
    x = s([1, 2, 3, 4, 5, 6])
    assert ind.sma(x, 3).tolist()[2:] == [2, 3, 4, 5]
    e = ind.ema(x, 3)
    assert e.iloc[:2].isna().all()
    assert e.iloc[2] == 2.0  # semilla = SMA de los 3 primeros
    assert e.iloc[3] == pytest.approx(0.5 * 4 + 0.5 * 2)  # alfa = 2/(3+1)


def test_ema_on_linear_series_lags_by_half_window():
    # En una recta, la EMA (sembrada con SMA) va exactamente (n-1)/2 por detrás.
    x = s(np.arange(100, dtype=float))
    for n in (12, 26, 50):
        e = ind.ema(x, n).dropna()
        np.testing.assert_allclose(e.to_numpy(), (x - (n - 1) / 2)[e.index].to_numpy())


def test_macd_on_linear_series():
    x = s(np.arange(120, dtype=float))
    m = ind.macd(x)
    # EMA12 va 5,5 atrás y EMA26 12,5 atrás: MACD = 7 desde el día 26, señal = 7 desde el día 34, histograma 0.
    assert m["macd"].iloc[:25].isna().all()
    np.testing.assert_allclose(m["macd"].iloc[25:], 7.0)
    assert m["macd_signal"].iloc[:33].isna().all()
    np.testing.assert_allclose(m["macd_signal"].iloc[33:], 7.0)
    np.testing.assert_allclose(m["macd_hist"].iloc[33:], 0.0, atol=1e-12)


def test_bollinger_uses_population_std():
    vals = [2, 4, 4, 4, 5, 5, 7, 9]  # media 5, desviación poblacional 2
    b = ind.bollinger(s(vals), n=8, k=2)
    last = b.iloc[-1]
    assert last.bb_mid == 5 and last.bb_upper == 9 and last.bb_lower == 1
    assert last.bb_pctb == pytest.approx((9 - 1) / 8)
    assert last.bb_width == pytest.approx(8 / 5)


def test_atr_constant_range_and_gap():
    n = 30
    close = s([100.0] * n)
    high, low = close + 1, close - 1
    a = ind.atr(high, low, close, 14)
    assert a.iloc[:13].isna().all()
    np.testing.assert_allclose(a.iloc[13:], 2.0)
    # Un salto: el rango verdadero usa el cierre anterior.
    high2, low2, close2 = high.copy(), low.copy(), close.copy()
    high2.iloc[-1], low2.iloc[-1], close2.iloc[-1] = 111, 109, 110
    tr = ind.true_range(high2, low2, close2)
    assert tr.iloc[-1] == 11  # |111 - 100|
    a2 = ind.atr(high2, low2, close2, 14)
    assert a2.iloc[-1] == pytest.approx((2.0 * 13 + 11) / 14)  # suavizado de Wilder


def test_relative_volume_excludes_today():
    v = s([100.0] * 25 + [300.0])
    rv = ind.relative_volume(v, 20)
    assert rv.iloc[:20].isna().all()
    assert rv.iloc[-2] == 1.0
    assert rv.iloc[-1] == 3.0


def test_compute_all_has_no_lookahead(bars):
    """Agregar días futuros no debe cambiar los indicadores ya calculados."""
    full = ind.compute_all(bars)
    partial = ind.compute_all(bars.iloc[:250])
    cols = [c for c in partial.columns if c not in ("open", "high", "low", "close", "volume")]
    pd.testing.assert_frame_equal(full.iloc[:250][cols], partial[cols])


def test_compute_all_columns_and_warmup(bars):
    df = ind.compute_all(bars)
    for col in ("sma20", "sma50", "sma200", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal",
                "macd_hist", "bb_upper", "bb_lower", "bb_pctb", "atr14", "atr14_pct", "rel_volume"):
        assert col in df.columns
    assert df["sma200"].iloc[198:199].isna().all() and df["sma200"].iloc[199:].notna().all()
    assert df["rsi14"].dropna().between(0, 100).all()


def test_basic_metrics(bars):
    m = ind.basic_metrics(bars)
    c = bars["close"]
    assert m["return_1m_pct"] == pytest.approx((c.iloc[-1] / c.iloc[-22] - 1) * 100)
    assert m["high_52w"] == pytest.approx(c.iloc[-252:].max())
    assert m["volatility_1y_pct"] > 0
    assert ind.basic_metrics(bars.iloc[:0]) == {}


def test_latest_snapshot_is_json_safe(bars):
    snap = ind.latest_snapshot(ind.compute_all(bars.iloc[:30]))
    assert snap["sma200"] is None  # NaN -> None
    assert isinstance(snap["close"], float)
