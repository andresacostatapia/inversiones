"""Tendencia: ¿el precio está en una tendencia alcista de largo plazo?

Tres componentes que suman hasta ±100:
- Precio frente a la SMA200 (±35): proporcional a la distancia, satura en ±5 %.
- SMA50 frente a la SMA200 (±35): cruce dorado (+) o cruce de la muerte (−).
- Pendiente de la EMA20 en 5 días (±30): satura en ±2 %.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..textfmt import days, pct
from .base import bars_since, crossed, valid


class TrendStrategy:
    key = "trend"
    label = "Tendencia"
    description = "Precio vs SMA200, SMA50 vs SMA200 (cruce dorado/de la muerte) y pendiente de la EMA20."

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        c, s50, s200, e20 = df["close"], df["sma50"], df["sma200"], df["ema20"]
        dist = (c / s200 - 1) * 100
        golden = (s50 > s200).where(valid(s50, s200))
        slope = (e20 / e20.shift(5) - 1) * 100
        p_dist = (dist / 5).clip(-1, 1) * 35
        p_cross = pd.Series(np.where(golden == True, 35.0, -35.0), index=df.index).where(golden.notna())  # noqa: E712
        p_slope = (slope / 2).clip(-1, 1) * 30
        score = p_dist + p_cross + p_slope
        return pd.DataFrame(
            {
                "score": score,
                "trend_dist200": dist,
                "trend_golden": golden,
                "trend_cross_age": bars_since(crossed(golden)),
                "trend_slope": slope,
            },
            index=df.index,
        )

    def explain(self, row: pd.Series) -> list[str]:
        if pd.isna(row.get("trend_dist200")) or pd.isna(row.get("trend_golden")):
            return ["Tendencia: faltan datos (se necesitan 200 días de historia)"]
        out = []
        d = row["trend_dist200"]
        out.append(f"Precio {pct(abs(d), signed=False)} {'sobre' if d >= 0 else 'bajo'} la SMA200 "
                   f"(tendencia de largo plazo {'alcista' if d >= 0 else 'bajista'})")
        age = row.get("trend_cross_age")
        golden = bool(row["trend_golden"])
        if pd.notna(age) and age <= 20:
            out.append(f"{'Cruce dorado' if golden else 'Cruce de la muerte'} (SMA50 cruzó "
                       f"{'sobre' if golden else 'bajo'} la SMA200) {days(age)}")
        else:
            out.append(f"SMA50 {'sobre' if golden else 'bajo'} la SMA200")
        sl = row.get("trend_slope")
        if pd.notna(sl):
            word = "subiendo" if sl > 0.2 else ("bajando" if sl < -0.2 else "plana")
            out.append(f"EMA20 {word} ({pct(sl)} en 5 días)")
        return out
