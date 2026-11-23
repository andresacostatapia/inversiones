"""Backtester simple, solo largo (sin cortos ni apalancamiento), todo dentro o todo fuera.

Reglas:
- La señal se calcula con el cierre del día t y se ejecuta en la APERTURA del día t+1 (sin mirar el futuro).
- Entra cuando el puntaje >= umbral de compra; sale cuando <= umbral de venta; en medio mantiene lo que tenga
  (igual que COMPRAR / MANTENER / VENDER).
- Cada compra y cada venta paga comisión + deslizamiento. Una posición abierta al final se valoriza a mercado.
- Se usan precios ajustados por dividendos y splits cuando están disponibles, para que estrategia y
  buy & hold se comparen con el retorno total.
- Buy & hold: compra en la misma apertura en que empieza a evaluarse la estrategia y no vende.
- Todas las estrategias empiezan el mismo día: el primero con 200 días de historia (para la SMA200).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import BacktestConfig, SignalsConfig
from .signals import active_weights, score_frame
from .strategies import STRATEGIES
from .textfmt import pct

WARNING = ("Resultados pasados no garantizan resultados futuros. Un backtest favorable puede deberse a "
           "sobreajuste: no ajustes pesos y umbrales hasta que el pasado se vea bien.")


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str | None
    exit_price: float | None
    return_pct: float          # neto de comisiones (las abiertas, a mercado y sin costo de salida)
    open: bool = False


@dataclass
class SimResult:
    equity: pd.Series                        # valor de 1 unidad invertida, día a día
    in_market: pd.Series                     # 1 si tenía posición ese día
    trades: list[Trade] = field(default_factory=list)


def adjusted(bars: pd.DataFrame) -> pd.DataFrame:
    """OHLC ajustados por dividendos y splits (si hay adj_close)."""
    if "adj_close" not in bars.columns or bars["adj_close"].isna().all():
        return bars[["open", "high", "low", "close", "volume"]].copy()
    f = (bars["adj_close"] / bars["close"]).fillna(1.0)
    out = bars[["open", "high", "low", "close", "volume"]].copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * f
    return out


def simulate(open_: pd.Series, close: pd.Series, score: pd.Series, buy: float, sell: float,
             cost: float, start: int) -> SimResult:
    """Simula la regla de entrada/salida desde el índice `start` (la decisión del día `start` se ejecuta en start+1)."""
    o, c, s = open_.to_numpy(float), close.to_numpy(float), score.to_numpy(float)
    idx = close.index
    n = len(c)
    eq = np.ones(n - start)
    pos_arr = np.zeros(n - start)
    trades: list[Trade] = []
    equity, pos, entry_px, entry_eq, entry_date = 1.0, 0, 0.0, 1.0, None
    for i in range(start + 1, n):
        sig = s[i - 1]
        target = pos
        if not np.isnan(sig):
            if pos == 0 and sig >= buy:
                target = 1
            elif pos == 1 and sig <= sell:
                target = 0
        if pos == 0 and target == 1:          # compra en la apertura
            entry_eq, entry_px, entry_date = equity, o[i], idx[i]  # capital antes de pagar la comisión
            equity *= (1 - cost)
            equity *= c[i] / o[i]
        elif pos == 1 and target == 1:        # mantiene
            equity *= c[i] / c[i - 1]
        elif pos == 1 and target == 0:        # vende en la apertura
            equity *= o[i] / c[i - 1]
            equity *= (1 - cost)
            trades.append(Trade(entry_date.date().isoformat(), round(entry_px, 4), idx[i].date().isoformat(),
                                round(o[i], 4), round((equity / entry_eq - 1) * 100, 2)))
        pos = target
        eq[i - start] = equity
        pos_arr[i - start] = pos
    if pos == 1:
        trades.append(Trade(entry_date.date().isoformat(), round(entry_px, 4), None, None,
                            round((equity / entry_eq - 1) * 100, 2), open=True))
    return SimResult(pd.Series(eq, index=idx[start:]), pd.Series(pos_arr, index=idx[start:]), trades)


def buy_and_hold(open_: pd.Series, close: pd.Series, cost: float, start: int) -> SimResult:
    always = pd.Series(np.inf, index=close.index)
    return simulate(open_, close, always, buy=0, sell=-np.inf, cost=cost, start=start)


def metrics(sim: SimResult) -> dict:
    eq = sim.equity
    if len(eq) < 2:
        return {}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    dd = eq / eq.cummax() - 1
    daily = eq.pct_change().dropna()
    vol = float(daily.std() * np.sqrt(ind.TRADING_DAYS)) if len(daily) > 1 else 0.0
    closed = [t for t in sim.trades if not t.open]
    wins = [t for t in closed if t.return_pct > 0]
    return {
        "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2) if total > -1 else -100.0,
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "volatility_pct": round(vol * 100, 2),
        "sharpe": round(float(daily.mean() * ind.TRADING_DAYS / (vol or np.nan)), 2) if vol else None,
        "trades": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        # Promedio en centésimas enteras: exacto, y así el motor TypeScript (app Android) da lo mismo.
        "avg_trade_pct": round(sum(round(t.return_pct * 100) for t in closed) / len(closed) / 100, 2) if closed else None,
        "exposure_pct": round(float(sim.in_market.iloc[1:].mean()) * 100, 1),
        "open_position": bool(sim.trades and sim.trades[-1].open),
    }


def _weekly(eq: pd.Series) -> list[dict]:
    w = eq.resample("W-FRI").last().dropna()
    if len(w) and w.index[-1] != eq.index[-1]:
        w.iloc[-1] = eq.iloc[-1]
    return [{"date": d.date().isoformat(), "value": round(float(v), 5)} for d, v in w.items()]


def _verdict(m: dict, bh: dict) -> str:
    if not m or not bh:
        return ""
    ret = "superó" if m["total_return_pct"] > bh["total_return_pct"] else "no superó"
    diff = m["max_drawdown_pct"] - bh["max_drawdown_pct"]
    dd = ("con la misma caída máxima" if abs(diff) < 0.05 else
          "con menor caída máxima" if diff > 0 else "con mayor caída máxima")
    return (f"{ret} a buy & hold en retorno ({pct(m['total_return_pct'])} vs {pct(bh['total_return_pct'])}), "
            f"{dd} ({pct(m['max_drawdown_pct'])} vs {pct(bh['max_drawdown_pct'])})")


def run_backtest(symbol: str, bars: pd.DataFrame, sig_cfg: SignalsConfig, bt_cfg: BacktestConfig,
                 years: float | None = None, strategies: list[str] | None = None,
                 include_curves: bool = True, include_trades: bool = True) -> dict:
    data = adjusted(bars.dropna(subset=["open", "close"]))
    df = ind.compute_all(data)
    frame = score_frame(df, sig_cfg)
    weights = active_weights(sig_cfg)
    keys = [k for k in (strategies or list(weights)) if k in STRATEGIES]

    # Primer día evaluable: todas las estrategias con datos (incluida la SMA200).
    ready = frame[[*keys, "composite"]].notna().all(axis=1) & df["sma200"].notna()
    if not ready.any():
        raise ValueError(f"{symbol}: historia insuficiente para el backtest (se necesitan más de 200 días)")
    start = int(np.argmax(ready.to_numpy()))
    if years:
        cutoff = df.index[-1] - pd.Timedelta(days=int(years * 365.25))
        start = max(start, int(np.searchsorted(df.index, cutoff)))
    if start >= len(df) - 2:
        raise ValueError(f"{symbol}: periodo demasiado corto para el backtest")

    cost = (bt_cfg.commission_pct + bt_cfg.slippage_pct) / 100
    buy, sell = sig_cfg.thresholds.buy, sig_cfg.thresholds.sell
    bh_sim = buy_and_hold(data["open"], data["close"], cost, start)
    bh = metrics(bh_sim)

    results = []
    for key in [*keys, "composite"]:
        sim = simulate(data["open"], data["close"], frame[key], buy, sell, cost, start)
        m = metrics(sim)
        item = {
            "key": key,
            "label": "Combinada" if key == "composite" else STRATEGIES[key].label,
            "metrics": m,
            "vs_buy_hold": {
                "return_diff_pct": round(m["total_return_pct"] - bh["total_return_pct"], 2),
                "drawdown_diff_pct": round(m["max_drawdown_pct"] - bh["max_drawdown_pct"], 2),
                "verdict": _verdict(m, bh),
            },
        }
        if include_curves:
            item["equity"] = _weekly(sim.equity)
        if include_trades:
            item["trades"] = [t.__dict__ for t in sim.trades]
        results.append(item)

    out = {
        "symbol": symbol,
        "start": df.index[start].date().isoformat(),
        "end": df.index[-1].date().isoformat(),
        "years": round((df.index[-1] - df.index[start]).days / 365.25, 2),
        "adjusted_prices": bool("adj_close" in bars.columns and bars["adj_close"].notna().any()),
        "assumptions": {
            "execution": "señal al cierre, ejecución en la apertura del día siguiente",
            "position": "solo largo, 100 % dentro o fuera",
            "commission_pct": bt_cfg.commission_pct,
            "slippage_pct": bt_cfg.slippage_pct,
            "thresholds": {"buy": buy, "sell": sell},
            "weights": weights,
        },
        "buy_hold": {"metrics": bh, **({"equity": _weekly(bh_sim.equity)} if include_curves else {})},
        "strategies": results,
        "warning": WARNING,
    }
    return out
