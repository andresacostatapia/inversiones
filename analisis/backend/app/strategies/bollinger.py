"""Bandas de Bollinger (20, 2σ): reversión a la media, filtrada por la tendencia.

Puntaje = (0,5 − %B) × 160, acotado a ±100: banda inferior (%B = 0) → +80, superior (%B = 1) → −80.
Filtro: un toque de la banda inferior en tendencia bajista (precio bajo la SMA200) se reduce a la mitad,
porque "comprar la caída" contra la tendencia falla más; lo mismo para vender una subida en tendencia alcista.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..textfmt import num


class BollingerStrategy:
    key = "bollinger"
    label = "Bollinger"
    description = "Precio cerca de la banda inferior (compra) o superior (venta), filtrado por la tendencia."

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        b = df["bb_pctb"]
        raw = ((0.5 - b) * 160).clip(-100, 100)
        above200 = df["close"] > df["sma200"]
        has200 = df["sma200"].notna()
        against = has200 & (((raw > 0) & ~above200) | ((raw < 0) & above200))
        score = pd.Series(np.where(against, raw / 2, raw), index=df.index).where(b.notna())
        return pd.DataFrame({"score": score, "bb_against_trend": against.where(b.notna())}, index=df.index)

    def explain(self, row: pd.Series) -> list[str]:
        b = row.get("bb_pctb")
        if pd.isna(b):
            return ["Bollinger: faltan datos"]
        if b <= 0:
            pos = "bajo la banda inferior"
        elif b < 0.2:
            pos = "cerca de la banda inferior"
        elif b <= 0.8:
            pos = "dentro de las bandas"
        elif b < 1:
            pos = "cerca de la banda superior"
        else:
            pos = "sobre la banda superior"
        out = [f"Precio {pos} de Bollinger (%B {num(b, 2)})"]
        if bool(row.get("bb_against_trend")):
            out.append("…pero va contra la tendencia de largo plazo: señal reducida a la mitad")
        return out
