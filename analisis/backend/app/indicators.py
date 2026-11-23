"""Indicadores técnicos sobre series diarias (pandas).

Convenciones (las mismas de TradingView y StockCharts, para que los valores coincidan):
- EMA: se inicializa con la SMA de los primeros `n` valores; antes de eso es NaN.
- RSI y ATR: suavizado de Wilder (RMA, alfa = 1/n), también inicializado con una SMA.
- Bollinger: desviación estándar poblacional (ddof=0).
- Volumen relativo: volumen del día / promedio de los `n` días ANTERIORES (no se incluye a sí mismo).

Todas las funciones reciben y devuelven `pd.Series` con el mismo índice; los primeros valores,
donde no hay historia suficiente, quedan como NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def _seeded_ewm(values: pd.Series, n: int, alpha: float) -> pd.Series:
    """Media exponencial recursiva inicializada con la SMA de los primeros `n` valores válidos."""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna()
    if len(valid) < n:
        return out
    arr = valid.to_numpy(dtype=float)
    res = np.empty(len(arr) - n + 1)
    res[0] = arr[:n].mean()
    for i in range(1, len(res)):
        res[i] = alpha * arr[n - 1 + i] + (1 - alpha) * res[i - 1]
    out.loc[valid.index[n - 1 :]] = res
    return out


def ema(close: pd.Series, n: int) -> pd.Series:
    return _seeded_ewm(close, n, 2.0 / (n + 1))


def rma(values: pd.Series, n: int) -> pd.Series:
    """Media de Wilder (la que usan RSI y ATR)."""
    return _seeded_ewm(values, n, 1.0 / n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = rma(gain, n)
    avg_loss = rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100 - 100 / (1 + rs)
    # Sin pérdidas: RSI 100. Sin ganancias ni pérdidas (precio plano): 50.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_b = (close - lower) / width
        bandwidth = width / mid
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_pctb": pct_b, "bb_width": bandwidth})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    avg = volume.shift(1).rolling(n, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rv = volume / avg
    return rv.where(avg > 0)


def compute_all(bars: pd.DataFrame) -> pd.DataFrame:
    """Agrega todas las columnas de indicadores a un DataFrame OHLCV (columnas en minúscula)."""
    df = bars.copy()
    c = df["close"]
    for n in (20, 50, 200):
        df[f"sma{n}"] = sma(c, n)
        df[f"ema{n}"] = ema(c, n)
    df["rsi14"] = rsi(c, 14)
    df = df.join(macd(c)).join(bollinger(c))
    df["atr14"] = atr(df["high"], df["low"], c, 14)
    df["atr14_pct"] = df["atr14"] / c * 100
    df["rel_volume"] = relative_volume(df["volume"], 20)
    return df


def _ret(c: pd.Series, days: int) -> float | None:
    if len(c) <= days:
        return None
    return float(c.iloc[-1] / c.iloc[-1 - days] - 1) * 100


def basic_metrics(bars: pd.DataFrame) -> dict[str, float | None]:
    """Métricas simples sobre la serie diaria: retornos, volatilidad, máximos y mínimos de 52 semanas."""
    c = bars["close"].dropna()
    if c.empty:
        return {}
    last_year = c.iloc[-TRADING_DAYS:]
    daily = c.pct_change().dropna()
    vol = float(daily.iloc[-TRADING_DAYS:].std() * np.sqrt(TRADING_DAYS) * 100) if len(daily) > 20 else None
    hi, lo = float(last_year.max()), float(last_year.min())
    s200 = sma(c, 200).iloc[-1]
    return {
        "return_1m_pct": _ret(c, 21),
        "return_3m_pct": _ret(c, 63),
        "return_6m_pct": _ret(c, 126),
        "return_1y_pct": _ret(c, TRADING_DAYS),
        "volatility_1y_pct": vol,
        "high_52w": hi,
        "low_52w": lo,
        "from_high_52w_pct": (float(c.iloc[-1]) / hi - 1) * 100,
        "dist_sma200_pct": None if pd.isna(s200) else (float(c.iloc[-1]) / float(s200) - 1) * 100,
    }


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and not np.isfinite(v):
        return None
    if isinstance(v, np.generic):
        return v.item()
    return v


def latest_snapshot(df: pd.DataFrame) -> dict:
    """Último valor de cada indicador, listo para JSON (NaN -> None)."""
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {k: _clean(row[k]) for k in df.columns}
