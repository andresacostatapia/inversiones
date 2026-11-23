"""RSI(14): sobreventa suma, sobrecompra resta.

Puntaje lineal por tramos: RSI 20 → +100, 30 → +60, 40 → +20, 50 → 0, 60 → −20, 70 → −60, 80 → −100.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..textfmt import days, num
from .base import bars_since

XP = [20, 30, 40, 60, 70, 80]
FP = [100, 60, 20, -20, -60, -100]


class RsiStrategy:
    key = "rsi"
    label = "RSI"
    description = "RSI(14) bajo 30 = sobreventa (compra), sobre 70 = sobrecompra (venta)."

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        r = df["rsi14"]
        score = pd.Series(np.interp(r.fillna(50), XP, FP), index=df.index).where(r.notna())
        exit_oversold = (r > 30) & (r.shift(1) <= 30)
        exit_overbought = (r < 70) & (r.shift(1) >= 70)
        return pd.DataFrame(
            {
                "score": score,
                "rsi_exit_oversold_age": bars_since(exit_oversold),
                "rsi_exit_overbought_age": bars_since(exit_overbought),
            },
            index=df.index,
        )

    def explain(self, row: pd.Series) -> list[str]:
        r = row.get("rsi14")
        if pd.isna(r):
            return ["RSI: faltan datos"]
        if r < 30:
            zone = "sobreventa"
        elif r < 40:
            zone = "cerca de sobreventa"
        elif r <= 60:
            zone = "zona neutral"
        elif r <= 70:
            zone = "cerca de sobrecompra"
        else:
            zone = "sobrecompra"
        out = [f"RSI {num(r, 0)}: {zone}"]
        a = row.get("rsi_exit_oversold_age")
        if pd.notna(a) and a <= 5 and 30 < r < 50:
            out.append(f"RSI salió de sobreventa {days(a)}")
        a = row.get("rsi_exit_overbought_age")
        if pd.notna(a) and a <= 5 and 50 < r < 70:
            out.append(f"RSI salió de sobrecompra {days(a)}")
        return out
