"""
backtest.py — Prueba histórica de la estrategia de CDF Plus.

Cómo funciona (sin "mirar el futuro"):
  1. Calcula el sesgo de cada vela en 15m / 1h / 4h con las MISMAS reglas que la app y el bot
     (`indicators.tf_score_series`, verificado por tests).
  2. Alinea las temporalidades por hora de CIERRE: en el cierre de una vela de 1H solo se usan
     velas de 4H y 15M ya cerradas.
  3. Aplica el filtro intermercado con las tendencias de los 6 activos en ese instante.
  4. Simula la cartera en orden cronológico: la señal se evalúa al cierre de la vela de 1H y se
     entra en la APERTURA de la siguiente, con SL/TP por ATR, coste de spread y los mismos límites
     de posiciones que el bot. Si en una vela se tocan SL y TP, se asume SL (conservador).

Limitaciones honestas:
  * yfinance solo ofrece 60 días de velas de 15m: en el horizonte de 2 años el bloque 15M
    (10 % del score + parte de la alineación) no existe y la estrategia es una aproximación.
  * El tamaño se calcula como % de riesgo exacto (sin redondeo a 0.01 lotes) y no incluye swaps
    ni comisiones (solo el spread típico de config.TYPICAL_SPREAD).
  * Resultados pasados no garantizan resultados futuros.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

import config
from config import ASSETS
from data_fetcher import DataFetchError, _yf_history, resample_ohlcv
from indicators import (add_indicators, intermarket_from_trends, intermarket_multiplier, technical_score_vec,
                        tf_score_series)

logger = logging.getLogger("cdfplus.backtest")

HORIZON_2Y = "2y"     # 1h + 4h (sin 15m)
HORIZON_60D = "60d"   # 15m + 1h + 4h (estrategia completa)

TF_DELTA = {tf: pd.Timedelta(minutes=m) for tf, m in config.TF_MINUTES.items()}


# ---------------------------------------------------------------------------
# Datos históricos
# ---------------------------------------------------------------------------
def load_history_yf(horizon: str = HORIZON_2Y) -> tuple[dict[str, dict[str, pd.DataFrame]], list[str]]:
    """Descarga historial de yfinance. Devuelve ({activo: {tf: df}}, avisos)."""
    history: dict[str, dict[str, pd.DataFrame]] = {}
    notes: list[str] = []
    for key, a in ASSETS.items():
        try:
            h1 = _yf_history(a.yf_symbol, "1h", "730d")
            frames = {"1h": h1, "4h": resample_ohlcv(h1, "4h")}
            if horizon == HORIZON_60D:
                frames["15m"] = _yf_history(a.yf_symbol, "15m", "60d")
            history[key] = frames
        except DataFetchError as exc:
            notes.append(f"{key}: {exc}")
    return history, notes


def load_history_mt5(broker: Any, bars: dict[str, int] | None = None) -> tuple[dict[str, dict[str, pd.DataFrame]], list[str]]:
    """Descarga historial desde un MT5Broker conectado (más velas de 15m que yfinance)."""
    bars = bars or {"15m": 50_000, "1h": 15_000, "4h": 4_000}
    mt5 = broker.mt5
    history: dict[str, dict[str, pd.DataFrame]] = {}
    notes: list[str] = []
    from data_fetcher import clean_ohlcv
    for key, sym in broker.symbols.items():
        frames: dict[str, pd.DataFrame] = {}
        for tf, attr in broker.TF_MAP.items():
            rates = mt5.copy_rates_from_pos(sym, getattr(mt5, attr), 0, bars[tf])
            if rates is None or len(rates) == 0:
                notes.append(f"{key} {tf}: sin historial")
                continue
            df = pd.DataFrame(rates)
            df.index = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.rename(columns={"tick_volume": "volume"})
            frames[tf] = clean_ohlcv(df[["open", "high", "low", "close", "volume"]])
        if "1h" in frames and "4h" in frames:
            history[key] = frames
    return history, notes


# ---------------------------------------------------------------------------
# Scores históricos
# ---------------------------------------------------------------------------
def _aligned(score: pd.Series, tf: str, close_times: pd.DatetimeIndex, min_bars: int) -> pd.Series:
    """Score de una temporalidad disponible en cada hora de cierre (solo velas ya cerradas)."""
    s = score.iloc[min_bars:] if len(score) > min_bars else score.iloc[0:0]
    by_close = pd.Series(s.to_numpy(), index=s.index + TF_DELTA[tf])
    by_close = by_close[~by_close.index.duplicated(keep="last")]
    return by_close.reindex(close_times, method="ffill")


def compute_scores(history: dict[str, dict[str, pd.DataFrame]], use_intermarket: bool = True,
                   warmup: int = config.MIN_BARS) -> dict[str, pd.DataFrame]:
    """
    Devuelve, por activo, un DataFrame indexado por la APERTURA de cada vela de 1H con:
    open/high/low/close/atr, s15/s1h/s4h, technical, multiplier y score final (al cierre de la vela).
    """
    per_asset: dict[str, pd.DataFrame] = {}
    trends: dict[str, pd.Series] = {}
    for key, frames in history.items():
        d1 = add_indicators(frames["1h"])
        close_times = pd.DatetimeIndex(d1.index + TF_DELTA["1h"])
        s1 = pd.Series(tf_score_series(d1).to_numpy(), index=close_times)
        s1.iloc[:warmup] = np.nan
        s4 = _aligned(tf_score_series(add_indicators(frames["4h"])), "4h", close_times, warmup)
        if "15m" in frames and not frames["15m"].empty:
            s15 = _aligned(tf_score_series(add_indicators(frames["15m"])), "15m", close_times, warmup)
        else:
            s15 = pd.Series(np.nan, index=close_times)
        valid = s1.notna() & s4.notna()
        tech = technical_score_vec(s15, s1, s4).where(valid)
        df = pd.DataFrame({
            "open": d1["open"].to_numpy(), "high": d1["high"].to_numpy(), "low": d1["low"].to_numpy(),
            "close": d1["close"].to_numpy(), "atr": d1["atr"].to_numpy(),
            "s15": s15.to_numpy(), "s1h": s1.to_numpy(), "s4h": s4.to_numpy(), "technical": tech.to_numpy(),
        }, index=d1.index)
        df["close_time"] = close_times
        per_asset[key] = df
        trends[key] = pd.Series(((s1 + s4) / 2.0).where(valid).to_numpy(), index=close_times)

    # Filtro intermercado en cada instante (tendencias ffill sobre la línea temporal común)
    timeline = pd.DatetimeIndex(sorted(set().union(*[set(t.index) for t in trends.values()]))) if trends else None
    if timeline is not None and use_intermarket:
        tr = pd.DataFrame({k: v[~v.index.duplicated(keep="last")].reindex(timeline, method="ffill", limit=8)
                           for k, v in trends.items()})
        mults: dict[str, dict[pd.Timestamp, float]] = {k: {} for k in per_asset}
        tech_by_close = {k: pd.Series(df["technical"].to_numpy(), index=df["close_time"]) for k, df in per_asset.items()}
        tech_lookup = {k: s[~s.index.duplicated(keep="last")].to_dict() for k, s in tech_by_close.items()}
        for ts, row in tr.iterrows():
            available = {k: float(v) for k, v in row.items() if pd.notna(v)}
            if not available:
                continue
            ctx = intermarket_from_trends(available)
            for k in per_asset:
                t = tech_lookup[k].get(ts)
                if t is None or not np.isfinite(t):
                    continue
                direction = 1 if t > 0 else -1 if t < 0 else 0
                mults[k][ts] = intermarket_multiplier(k, direction, ctx)[0]
        for k, df in per_asset.items():
            m = pd.Series(mults[k]).reindex(df["close_time"]).fillna(1.0).to_numpy()
            df["multiplier"] = m
            df["score"] = np.clip(df["technical"].to_numpy() * m, -100, 100)
    else:
        for df in per_asset.values():
            df["multiplier"] = 1.0
            df["score"] = df["technical"]
    return per_asset


# ---------------------------------------------------------------------------
# Simulación de cartera
# ---------------------------------------------------------------------------
@dataclass
class BacktestParams:
    threshold: float = 70.0
    sl_atr_mult: float = config.DEFAULT_SL_ATR_MULT
    tp_atr_mult: float = config.DEFAULT_TP_ATR_MULT
    risk_pct: float = 0.5
    initial_equity: float = 10_000.0
    spread_mult: float = 1.0                # 1.0 = spread típico; 2.0 = el doble (prueba de estrés)
    apply_limits: bool = True               # Mismos límites de posiciones que el bot
    max_open_positions: int = 3
    max_index_positions: int = 2
    max_metal_positions: int = 1
    start: pd.Timestamp | None = None       # Solo se abren operaciones desde esta fecha


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series
    summary: dict[str, float]
    per_asset: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def _check_exit(d: int, sl: float, tp: float, o: float, h: float, low: float) -> tuple[float | None, str]:
    if d > 0:
        if o <= sl:
            return o, "gap SL"
        if o >= tp:
            return o, "gap TP"
        if low <= sl:
            return sl, "SL"
        if h >= tp:
            return tp, "TP"
    else:
        if o >= sl:
            return o, "gap SL"
        if o <= tp:
            return o, "gap TP"
        if h >= sl:
            return sl, "SL"
        if low <= tp:
            return tp, "TP"
    return None, ""


def simulate(scores: dict[str, pd.DataFrame], p: BacktestParams) -> BacktestResult:
    """Simulación cronológica de la cartera con las reglas de entrada/salida del bot."""
    events: list[tuple[pd.Timestamp, str, int]] = []  # (hora de apertura de vela, activo, posición)
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for key, df in scores.items():
        arrays[key] = {c: df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "atr", "score")}
        events.extend((ts, key, i) for i, ts in enumerate(df.index))
    events.sort(key=lambda e: (e[0], e[1]))

    equity = p.initial_equity
    curve: list[tuple[pd.Timestamp, float]] = []
    trades: list[dict[str, Any]] = []
    open_pos: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}

    def count(cat: str | None = None) -> int:
        keys = list(open_pos) + list(pending)
        return len(keys) if cat is None else sum(1 for k in keys if ASSETS[k].category == cat)

    for ts, key, i in events:
        a = arrays[key]
        o, h, low = a["open"][i], a["high"][i], a["low"][i]

        # 1) Entrada pendiente: se ejecuta en la apertura de esta vela
        if key in pending:
            pd_ = pending.pop(key)
            spread = config.TYPICAL_SPREAD.get(key, 0.0) * p.spread_mult
            d = pd_["dir"]
            entry = o + d * spread              # El spread se paga completo al entrar
            sl_dist = p.sl_atr_mult * pd_["atr"]
            open_pos[key] = {**pd_, "entry": entry, "entry_time": ts, "sl_dist": sl_dist,
                             "sl": entry - d * sl_dist, "tp": entry + d * p.tp_atr_mult * pd_["atr"],
                             "risk": equity * p.risk_pct / 100.0}

        # 2) Salidas en esta vela
        if key in open_pos:
            pos = open_pos[key]
            exit_px, reason = _check_exit(pos["dir"], pos["sl"], pos["tp"], o, h, low)
            if exit_px is not None:
                r = (exit_px - pos["entry"]) * pos["dir"] / pos["sl_dist"]
                pnl = r * pos["risk"]
                equity += pnl
                curve.append((ts, equity))
                trades.append({"activo": key, "dirección": "COMPRA" if pos["dir"] > 0 else "VENTA",
                               "score": round(pos["score"], 1), "entrada_hora": pos["entry_time"], "salida_hora": ts,
                               "entrada": pos["entry"], "salida": exit_px, "motivo": reason,
                               "R": r, "P/L": pnl, "equity": equity})
                del open_pos[key]

        # 3) Nueva señal al cierre de esta vela
        score, atr = a["score"][i], a["atr"][i]
        if (key in open_pos or key in pending or not np.isfinite(score) or not np.isfinite(atr) or atr <= 0
                or abs(score) < p.threshold or i + 1 >= len(a["open"])):
            continue
        if p.start is not None and ts < p.start:
            continue
        if p.apply_limits:
            cat = ASSETS[key].category
            if count() >= p.max_open_positions:
                continue
            if cat == "index" and count("index") >= p.max_index_positions:
                continue
            if cat == "metal" and count("metal") >= p.max_metal_positions:
                continue
        pending[key] = {"dir": 1 if score > 0 else -1, "atr": atr, "score": score}

    # Posiciones abiertas al final: se cierran al último cierre
    for key, pos in open_pos.items():
        df = scores[key]
        exit_px = float(df["close"].iloc[-1])
        r = (exit_px - pos["entry"]) * pos["dir"] / pos["sl_dist"]
        pnl = r * pos["risk"]
        equity += pnl
        ts = df.index[-1]
        curve.append((ts, equity))
        trades.append({"activo": key, "dirección": "COMPRA" if pos["dir"] > 0 else "VENTA",
                       "score": round(pos["score"], 1), "entrada_hora": pos["entry_time"], "salida_hora": ts,
                       "entrada": pos["entry"], "salida": exit_px, "motivo": "fin de datos",
                       "R": r, "P/L": pnl, "equity": equity})

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values("salida_hora").reset_index(drop=True)
        trades_df["equity"] = p.initial_equity + trades_df["P/L"].cumsum()
    eq = pd.Series([p.initial_equity] + ([] if trades_df.empty else trades_df["equity"].tolist()),
                   index=[_first_time(scores)] + ([] if trades_df.empty else trades_df["salida_hora"].tolist()))
    return BacktestResult(trades_df, eq, summarize(trades_df, eq, p), per_asset_table(trades_df))


def _first_time(scores: dict[str, pd.DataFrame]) -> pd.Timestamp:
    starts = [df.index[df["score"].notna().argmax()] for df in scores.values() if df["score"].notna().any()]
    return min(starts) if starts else pd.Timestamp.now(tz="UTC")


def summarize(trades: pd.DataFrame, equity: pd.Series, p: BacktestParams) -> dict[str, float]:
    """Métricas clave: nº operaciones, acierto, profit factor, expectativa en R, retorno y drawdown."""
    if trades.empty:
        return {"operaciones": 0, "acierto_%": 0.0, "profit_factor": 0.0, "expectativa_R": 0.0,
                "R_total": 0.0, "retorno_%": 0.0, "max_drawdown_%": 0.0, "operaciones_mes": 0.0}
    r = trades["R"]
    gains, losses = trades.loc[trades["P/L"] > 0, "P/L"].sum(), -trades.loc[trades["P/L"] < 0, "P/L"].sum()
    peak = equity.cummax()
    dd = ((equity - peak) / peak * 100.0).min()
    span_days = max(1.0, (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0)
    return {
        "operaciones": int(len(trades)),
        "acierto_%": float((r > 0).mean() * 100.0),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf") if gains > 0 else 0.0,
        "expectativa_R": float(r.mean()),
        "R_total": float(r.sum()),
        "retorno_%": float((equity.iloc[-1] / p.initial_equity - 1.0) * 100.0),
        "max_drawdown_%": float(dd),
        "operaciones_mes": float(len(trades) / span_days * 30.0),
    }


def per_asset_table(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["activo", "operaciones", "acierto_%", "expectativa_R", "R_total", "P/L"])
    g = trades.groupby("activo")
    out = pd.DataFrame({
        "operaciones": g.size(),
        "acierto_%": g["R"].apply(lambda s: (s > 0).mean() * 100.0),
        "expectativa_R": g["R"].mean(),
        "R_total": g["R"].sum(),
        "P/L": g["P/L"].sum(),
    }).reset_index()
    return out.sort_values("R_total", ascending=False).reset_index(drop=True)


def threshold_sweep(scores: dict[str, pd.DataFrame], base: BacktestParams,
                    thresholds: tuple[float, ...] = (30, 40, 50, 60, 70, 80)) -> pd.DataFrame:
    """Repite la simulación con varios umbrales de entrada para ver la sensibilidad."""
    rows = []
    for th in thresholds:
        params = BacktestParams(**{**base.__dict__, "threshold": float(th)})
        s = simulate(scores, params).summary
        rows.append({"umbral": th, **s})
    return pd.DataFrame(rows)


def verdict(summary: dict[str, float]) -> tuple[str, str]:
    """Lectura prudente del resultado: (nivel, texto)."""
    n, pf, exp = summary["operaciones"], summary["profit_factor"], summary["expectativa_R"]
    if n < 30:
        return "warn", (f"Solo {n} operaciones: muestra demasiado pequeña para sacar conclusiones. "
                        "Baja el umbral o usa un horizonte más largo.")
    if pf < 1.0 or exp <= 0:
        return "bad", ("La estrategia PIERDE en este periodo con estos parámetros. "
                       "No la automatices en real.")
    if pf < 1.3 or exp < 0.1:
        return "warn", ("Ventaja débil: puede desaparecer con spreads reales, swaps o deslizamiento. "
                        "Pruébala semanas en demo antes de cualquier decisión.")
    return "good", ("Resultado positivo en el histórico. Aun así, valida semanas en demo: el pasado no "
                    "garantiza el futuro y puede haber sobreajuste.")
