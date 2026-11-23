"""Registro de estrategias. Para agregar una: crear el módulo con compute/explain y sumarla aquí."""

from .base import Strategy
from .bollinger import BollingerStrategy
from .macd import MacdStrategy
from .rsi import RsiStrategy
from .trend import TrendStrategy

STRATEGIES: dict[str, Strategy] = {
    s.key: s for s in (TrendStrategy(), RsiStrategy(), MacdStrategy(), BollingerStrategy())
}

__all__ = ["STRATEGIES", "Strategy"]
