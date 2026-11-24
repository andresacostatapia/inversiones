"""MACD (12, 26, 9).

- Línea MACD sobre su señal (+40) o bajo ella (−40).
- Cruce en los últimos 3 días: refuerza ±40 (un cruce fresco pesa más que uno viejo).
- MACD sobre cero (+20) o bajo cero (−20): momentum de fondo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..textfmt import days
from .base import bars_since, crossed, valid

FRESH_DAYS = 3


class MacdStrategy:
    key = "macd"
    label = "MACD"
    description = "Posición del MACD frente a su señal, cruces recientes y signo del MACD."

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        m, s = df["macd"], df["macd_signal"]
        ok = valid(m, s)
        above = (m > s).where(ok)
        age = bars_since(crossed(above))
        sign = np.where(above == True, 1.0, -1.0)  # noqa: E712
        base = 40 * sign
        fresh = np.where(age <= FRESH_DAYS, 40 * sign, 0.0)
        zero = np.where(m > 0, 20.0, -20.0)
        score = pd.Series(base + fresh + zero, index=df.index).where(ok)
        return pd.DataFrame({"score": score, "macd_above": above, "macd_cross_age": age}, index=df.index)

    def explain(self, row: pd.Series) -> list[str]:
        if pd.isna(row.get("macd_above")):
            return ["MACD: faltan datos"]
        above = bool(row["macd_above"])
        age = row.get("macd_cross_age")
        out = []
        if pd.notna(age) and age <= 10:
            out.append(f"Cruce MACD {'alcista' if above else 'bajista'} {days(age)}")
        else:
            out.append(f"MACD {'sobre' if above else 'bajo'} su línea de señal")
        out.append(f"MACD {'positivo' if row['macd'] > 0 else 'negativo'} (momentum de fondo "
                   f"{'alcista' if row['macd'] > 0 else 'bajista'})")
        return out
