"""Contrato de las estrategias.

Cada estrategia trabaja sobre el DataFrame de indicadores (ver indicators.compute_all) y devuelve,
para TODOS los días, un puntaje de -100 (vender) a +100 (comprar) más las columnas auxiliares que usa
para explicarse. Así el mismo cálculo sirve para la señal de hoy y para el backtest: no hay dos versiones
de la lógica que puedan divergir. NaN = no hay historia suficiente para opinar ese día.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd


class Strategy(Protocol):
    key: str
    label: str
    description: str

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Columna `score` (-100..100, NaN sin datos) + columnas auxiliares con prefijo propio."""
        ...

    def explain(self, row: pd.Series) -> list[str]:
        """Razones en español para un día (`row` = indicadores + columnas de compute)."""
        ...


def bars_since(event: pd.Series) -> pd.Series:
    """Velas transcurridas desde la última vez que `event` fue verdadero (0 = hoy, NaN = nunca)."""
    idx = np.arange(len(event), dtype=float)
    last = pd.Series(np.where(event.fillna(False).to_numpy(dtype=bool), idx, np.nan), index=event.index).ffill()
    return pd.Series(idx, index=event.index) - last


def crossed(flag: pd.Series) -> pd.Series:
    """Verdadero el día en que una condición booleana cambia (con datos válidos el día anterior)."""
    prev = flag.shift(1)
    return (flag != prev) & prev.notna() & flag.notna()


def valid(*series: pd.Series) -> pd.Series:
    out = pd.Series(True, index=series[0].index)
    for s in series:
        out &= s.notna()
    return out
