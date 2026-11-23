"""Exporta casos de prueba "dorados" desde el motor de Python para validar el motor de TypeScript (app Android).

    cd analisis/backend && python scripts/export_fixtures.py

Escribe frontend/src/core/__fixtures__/golden.json con entradas fijas y lo que Python calcula para ellas.
El test frontend/src/core/golden.test.ts exige que TypeScript dé lo mismo. Si cambias la lógica en Python,
vuelve a correr este script y ajusta TypeScript hasta que el test pase.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import indicators as ind  # noqa: E402
from app.backtest import run_backtest  # noqa: E402
from app.config import BacktestConfig, SignalsConfig  # noqa: E402
from app.market_hours import freshness, is_open  # noqa: E402
from app.portfolio import Candidate, PriceInfo, Tx, build_positions, suggest_contribution, value_portfolio  # noqa: E402
from app.signals import evaluate, score_frame  # noqa: E402

OUT = ROOT.parent / "frontend" / "src" / "core" / "__fixtures__" / "golden.json"


def clean(v):
    """JSON sin NaN/inf (→ null) y con tipos nativos."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if not math.isfinite(f) else f
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()[:10] if isinstance(v, (pd.Timestamp, date)) and not isinstance(v, datetime) else v.isoformat()
    if v is None or isinstance(v, (str, int)):
        return v
    if pd.isna(v):
        return None
    return v


def make_bars(n: int, seed: int, drift: float = 0.0004, vol: float = 0.012, start: str = "2018-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, name="date")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, n)))
    volume = rng.integers(1_000_000, 3_000_000, n).astype(float)
    # Algunos días con volumen alto/bajo para ejercitar el factor de volumen.
    volume[::37] *= 2.5
    volume[::53] *= 0.4
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    return df.round(6)


def bars_json(df: pd.DataFrame) -> dict:
    out = {"date": [d.date().isoformat() for d in df.index]}
    for c in df.columns:
        out[c] = [clean(x) for x in df[c].tolist()]
    return out


def frame_json(df: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        s = df[c]
        out[c] = [clean(None if (isinstance(x, float) and math.isnan(x)) else (float(x) if isinstance(x, (bool, np.bool_)) else x))
                  for x in s.tolist()]
    return out


def main() -> None:
    cfg = SignalsConfig()
    cases = {}

    # 1. Indicadores + estrategias + señal sobre tres series (alcista, bajista, lateral).
    series = {
        "up": make_bars(520, 7, drift=0.0012),
        "down": make_bars(520, 11, drift=-0.0012),
        "flat": make_bars(520, 3, drift=0.0, vol=0.015),
    }
    ind_cols = ["sma20", "ema20", "sma50", "ema50", "sma200", "ema200", "rsi14", "macd", "macd_signal", "macd_hist",
                "bb_mid", "bb_upper", "bb_lower", "bb_pctb", "bb_width", "atr14", "atr14_pct", "rel_volume"]
    frame_cols = ["trend", "rsi", "macd", "bollinger", "trend_dist200", "trend_golden", "trend_cross_age", "trend_slope",
                  "rsi_exit_oversold_age", "rsi_exit_overbought_age", "macd_above", "macd_cross_age",
                  "bb_against_trend", "raw", "available_weight", "volume_factor", "composite", "vol_ratio"]
    for name, bars in series.items():
        df = ind.compute_all(bars)
        frame = score_frame(df, cfg)
        # La señal de varios días (no solo el último), para cubrir más casos de razones y confianza.
        evals = []
        for cut in (60, 210, 260, 333, 400, 451, 519):
            sub = df.iloc[: cut + 1]
            for prov, stale in ((False, False), (True, False), (False, True)):
                r = evaluate(name, sub, cfg, provisional=prov, stale=stale)
                evals.append({"cut": cut, "provisional": prov, "stale": stale, "result": clean(r.to_dict())})
        cases[name] = {
            "bars": bars_json(bars),
            "indicators": frame_json(df, ind_cols),
            "frame": frame_json(frame, frame_cols),
            "metrics": clean(ind.basic_metrics(df)),
            "evals": evals,
        }

    # 2. Configuración distinta: pesos, umbrales y sin volumen.
    alt = SignalsConfig(thresholds={"buy": 20, "sell": -15}, weights={"trend": 2, "rsi": 1, "macd": 0, "bollinger": 1},
                        volume_effect=0.3)
    df = ind.compute_all(series["flat"])
    cases["alt_config"] = {
        "config": clean(alt.model_dump()),
        "frame": frame_json(score_frame(df, alt), ["trend", "rsi", "bollinger", "raw", "volume_factor", "composite"]),
        "eval": clean(evaluate("flat", df, alt).to_dict()),
    }

    # 3. Backtest con precios ajustados por dividendos.
    bt_bars = make_bars(900, 21, drift=0.0005)
    bt_bars["adj_close"] = (bt_bars["close"] * np.linspace(0.9, 1.0, len(bt_bars))).round(6)
    cases["backtest"] = {
        "bars": bars_json(bt_bars),
        "result": clean(run_backtest("X", bt_bars, cfg, BacktestConfig(commission_pct=0.5, slippage_pct=0.1), years=2)),
        "result_all": clean(run_backtest("X", bt_bars, cfg, BacktestConfig(commission_pct=0.0), years=None,
                                         strategies=["trend", "rsi"])),
    }

    # 4. Cartera, valorización y aporte.
    txs = [
        Tx("SPY", "compra", 6, 650, "USD", date(2025, 12, 1), 1, 935, 1),
        Tx("SPY", "venta", 1, 740, "USD", date(2026, 9, 1), 1, 960, 2),
        Tx("VOO", "compra", 4, 600, "USD", date(2026, 2, 3), 1, 950, 3),
        Tx("SQM-B.SN", "compra", 40, 70000, "CLP", date(2026, 3, 10), 1500, 950, 4),
        Tx("CHILE.SN", "compra", 5000, 165, "CLP", date(2026, 4, 15), 800, 945, 5),
        Tx("EEM", "compra", 30, 52, "USD", date(2026, 5, 20), 1, 940, 6),
    ]
    prices = {"SPY": PriceInfo(722.29, 689.81, "USD"), "VOO": PriceInfo(663.91, 635.5, "USD"),
              "SQM-B.SN": PriceInfo(49780, 48800, "CLP"), "CHILE.SN": PriceInfo(188.35, 189.2, "CLP"),
              "EEM": PriceInfo(57.83, 53.86, "USD")}
    pos = build_positions(txs)
    val = value_portfolio(pos, prices, 1016.0, {"CL": 0.3, "INTL": 0.7}, -20, -25)
    cands = [
        Candidate("SQM-B.SN", 49780, "CLP", 49780, "MANTENER", True, None, "vender", -28.9),
        Candidate("CHILE.SN", 188.35, "CLP", 188.35, "MANTENER", True, None, "ok", 14.0),
        Candidate("FALABELLA.SN", 6778, "CLP", 6778, "COMPRAR", False),
        Candidate("SPY", 722.29, "USD", 722.29 * 1016, "MANTENER", True, None, "ok", 11.1),
        Candidate("VOO", 663.91, "USD", 663.91 * 1016, "VENDER", True, None, "ok", 10.6),
        Candidate("EEM", 57.83, "USD", 57.83 * 1016, None, True),
    ]
    bucket_values = {a["bucket"]: a["value_clp"] for a in val["allocation"]}
    contributions = []
    for amount, frac in ((500_000, {"CL": False, "US": True}), (3_000_000, {"CL": False, "US": False}),
                         (20_000_000, {"CL": True, "US": True})):
        contributions.append({"amount": amount, "fractional": frac,
                              "result": clean(suggest_contribution(amount, bucket_values, cands,
                                                                   {"CL": 0.3, "INTL": 0.7}, frac).to_dict())})
    cases["portfolio"] = {
        "txs": [clean({"symbol": t.symbol, "side": t.side, "quantity": t.quantity, "price": t.price,
                       "currency": t.currency, "date": t.date.isoformat(), "fee": t.fee, "fx_rate": t.fx_rate,
                       "id": t.id}) for t in txs],
        "prices": {k: clean(v.__dict__) for k, v in prices.items()},
        "valuation": clean(val),
        "candidates": [clean(c.__dict__) for c in cands],
        "bucket_values": clean(bucket_values),
        "contributions": contributions,
    }

    # 5. Horarios y etiquetas de frescura.
    utc = timezone.utc
    stamps = [datetime(2026, 11, 23, h, m, tzinfo=utc) for h, m in ((5, 0), (12, 29), (12, 30), (15, 0), (19, 59),
                                                                     (20, 0), (21, 0), (22, 0))]
    stamps += [datetime(2026, 11, 26, 16, 0, tzinfo=utc), datetime(2026, 11, 27, 17, 59, tzinfo=utc),
               datetime(2026, 11, 27, 18, 0, tzinfo=utc), datetime(2026, 7, 6, 13, 45, tzinfo=utc),
               datetime(2026, 12, 25, 15, 0, tzinfo=utc), datetime(2027, 1, 4, 13, 0, tzinfo=utc)]
    hours = [{"t": s.isoformat(), "CL": is_open("CL", s), "US": is_open("US", s), "FX": is_open("FX", s)} for s in stamps]
    now = datetime(2026, 11, 23, 15, 0, tzinfo=utc)
    fresh = []
    for age_min, rt, open_, daily in ((0.5, True, True, False), (1, True, True, False), (5, True, True, False),
                                      (16, False, True, False), (45, False, True, False), (200, False, True, False),
                                      (60 * 72, False, False, False), (60 * 12, False, True, True), (0.2, False, True, False)):
        as_of = now - timedelta(minutes=age_min)
        fresh.append({"as_of": as_of.isoformat(), "realtime": rt, "market_open": open_, "daily": daily,
                      "result": freshness(as_of, realtime=rt, market_open=open_, now=now, daily=daily)})
    cases["market"] = {"hours": hours, "now": now.isoformat(), "freshness": clean(fresh)}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
