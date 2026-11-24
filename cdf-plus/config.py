"""
config.py — Configuración centralizada de CDF Plus.

Aquí viven TODOS los parámetros ajustables de la aplicación:
  * Mapeo de cada CFD de XTB a sus símbolos en Twelve Data y yfinance.
  * Factor de escala (cuando el feed usa un ETF como SPY/QQQ/DIA/IWM en lugar del índice).
  * Valor monetario del punto por 1.0 lote estándar en XTB.
  * Parámetros de indicadores, pesos del motor de scoring y umbrales.

IMPORTANTE: los valores de contrato (USD por punto por lote, margen) son los habituales
en XTB, pero XTB puede cambiarlos según la entidad/región. Verifícalos en xStation
(clic derecho sobre el instrumento → "Información del instrumento") y, si difieren,
corrígelos aquí o desde la barra lateral de la app ("Especificaciones de contrato").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Rutas y metadatos
# ---------------------------------------------------------------------------
APP_NAME: Final[str] = "CDF Plus"
APP_SUBTITLE: Final[str] = "Copiloto de Trading CFD · XTB xStation"
BASE_DIR: Final[Path] = Path(__file__).resolve().parent
STATE_DIR: Final[Path] = BASE_DIR / ".state"          # Offsets y alertas persistidas
OFFSETS_FILE: Final[Path] = STATE_DIR / "offsets.json"
ALERTS_FILE: Final[Path] = STATE_DIR / "alerts_sent.json"


# ---------------------------------------------------------------------------
# Definición de activos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AssetConfig:
    """Configuración de un CFD de XTB y de cómo obtener sus datos."""

    key: str                    # Símbolo en xStation (XTB)
    name: str                   # Nombre legible
    category: str               # "index" (índice USA) o "metal" (refugio)
    # --- Twelve Data ---
    twelve_symbol: str          # Símbolo a consultar en Twelve Data
    twelve_scale: float         # Multiplicador feed → precio índice (ETF→índice)
    # --- yfinance (fallback) ---
    yf_symbol: str              # Ticker de Yahoo Finance
    yf_scale: float             # Multiplicador feed → precio índice
    # --- Especificación del contrato en XTB ---
    point_value_usd: float      # USD que vale 1.0 punto de precio con 1.0 lote
    margin_rate: float          # Margen requerido (0.05 = 5 % ≈ apalancamiento 1:20)
    digits: int                 # Decimales del precio en xStation
    min_lot: float = 0.01       # Lote mínimo
    lot_step: float = 0.01      # Incremento de lote
    max_lot: float = 100.0      # Lote máximo razonable (protección ante errores)
    # Símbolos alternativos (documentación para planes de pago de Twelve Data)
    twelve_alternatives: tuple[str, ...] = field(default_factory=tuple)


ASSETS: Final[dict[str, AssetConfig]] = {
    "GOLD": AssetConfig(
        key="GOLD",
        name="Oro Spot",
        category="metal",
        twelve_symbol="XAU/USD",
        twelve_scale=1.0,
        yf_symbol="GC=F",
        yf_scale=1.0,
        point_value_usd=100.0,      # 1 lote = 100 oz → 1 USD de movimiento = 100 USD
        margin_rate=0.05,
        digits=2,
    ),
    "SILVER": AssetConfig(
        key="SILVER",
        name="Plata Spot",
        category="metal",
        twelve_symbol="XAG/USD",
        twelve_scale=1.0,
        yf_symbol="SI=F",
        yf_scale=1.0,
        point_value_usd=5000.0,     # 1 lote = 5 000 oz → 1 USD de movimiento = 5 000 USD
        margin_rate=0.10,
        digits=3,
    ),
    "US500": AssetConfig(
        key="US500",
        name="S&P 500",
        category="index",
        twelve_symbol="SPY",        # Plan gratuito: ETF. Con plan de pago: "SPX" y escala 1.0
        twelve_scale=10.0,          # SPX ≈ SPY × 10 (el offset corrige el residuo)
        yf_symbol="ES=F",
        yf_scale=1.0,
        point_value_usd=50.0,
        margin_rate=0.05,
        digits=1,
        twelve_alternatives=("SPX",),
    ),
    "US100": AssetConfig(
        key="US100",
        name="Nasdaq 100",
        category="index",
        twelve_symbol="QQQ",
        twelve_scale=41.0,          # NDX ≈ QQQ × 41
        yf_symbol="NQ=F",
        yf_scale=1.0,
        point_value_usd=20.0,
        margin_rate=0.05,
        digits=1,
        twelve_alternatives=("NDX",),
    ),
    "US30": AssetConfig(
        key="US30",
        name="Dow Jones 30",
        category="index",
        twelve_symbol="DIA",
        twelve_scale=100.0,         # DJI ≈ DIA × 100
        yf_symbol="YM=F",
        yf_scale=1.0,
        point_value_usd=5.0,
        margin_rate=0.05,
        digits=1,
        twelve_alternatives=("DJI",),
    ),
    "US2000": AssetConfig(
        key="US2000",
        name="Russell 2000",
        category="index",
        twelve_symbol="IWM",
        twelve_scale=10.1,          # RUT ≈ IWM × 10.1
        yf_symbol="RTY=F",
        yf_scale=1.0,
        point_value_usd=50.0,
        margin_rate=0.05,
        digits=2,
        twelve_alternatives=("RUT",),
    ),
}

INDEX_KEYS: Final[tuple[str, ...]] = tuple(k for k, a in ASSETS.items() if a.category == "index")
METAL_KEYS: Final[tuple[str, ...]] = tuple(k for k, a in ASSETS.items() if a.category == "metal")


# ---------------------------------------------------------------------------
# Temporalidades y fuentes de datos
# ---------------------------------------------------------------------------
TIMEFRAMES: Final[tuple[str, ...]] = ("15m", "1h", "4h")
OPERATIONAL_TF: Final[str] = "1h"      # Temporalidad principal de la señal y del ATR por defecto

# Duración en minutos de cada vela (para detectar datos atrasados / mercado cerrado)
TF_MINUTES: Final[dict[str, int]] = {"15m": 15, "1h": 60, "4h": 240}

# Cuántas velas se considera "atrasado" el último dato antes de marcar mercado cerrado
STALE_BARS_TOLERANCE: Final[float] = 3.0

# Twelve Data: se descarga SOLO la serie 15 min (1 crédito por activo) y se remuestrea
# localmente a 1h y 4h. Así 6 activos = 6 créditos por actualización (plan gratuito: 8/min).
TWELVE_DATA_URL: Final[str] = "https://api.twelvedata.com/time_series"
TWELVE_OUTPUTSIZE: Final[int] = 5000
TWELVE_MAX_REQUESTS_PER_MIN: Final[int] = 8
TWELVE_TIMEOUT_S: Final[float] = 20.0
TWELVE_CACHE_TTL_S: Final[int] = 300   # 5 min → ~72 créditos/hora, dentro de 800/día en jornada

# yfinance: 15m directo (máx. 60 días) y 1h directo (máx. 730 días); 4h remuestreado de 1h.
YF_PERIODS: Final[dict[str, str]] = {"15m": "30d", "1h": "180d"}
YF_CACHE_TTL_S: Final[int] = 120

# Velas mínimas para considerar un timeframe analizable (EMA 200 necesita historial)
MIN_BARS: Final[int] = 210

# Velas mostradas en el gráfico
CHART_BARS: Final[int] = 160


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IndicatorParams:
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14


INDICATORS: Final[IndicatorParams] = IndicatorParams()


# ---------------------------------------------------------------------------
# Motor de scoring
# ---------------------------------------------------------------------------
# Puntos de cada regla dentro de UNA temporalidad (suman 100 → sesgo -100..+100)
TF_RULE_WEIGHTS: Final[dict[str, float]] = {
    "ema200": 25.0,      # Precio sobre/bajo EMA 200 (tendencia estructural)
    "ema_stack": 25.0,   # Alineación EMA 20 > 50 > 200 (o inversa)
    "macd_cross": 15.0,  # MACD sobre/bajo su línea de señal
    "macd_hist": 10.0,   # Histograma MACD acelerando a favor
    "rsi": 15.0,         # RSI en zona de momentum
    "ema20": 10.0,       # Precio sobre/bajo EMA 20 (impulso corto)
}

# Contribución de cada bloque al score final (suman 100)
SCORE_BLOCKS: Final[dict[str, float]] = {
    "operational": 60.0,  # Reglas completas en la temporalidad operativa (1h)
    "higher_tf": 15.0,    # Sesgo de la temporalidad mayor (4h)
    "timing_tf": 10.0,    # Sesgo de la temporalidad de entrada (15m)
    "mtf_align": 15.0,    # Bonus si 15m / 1h / 4h apuntan en la misma dirección
}

# Umbral de sesgo por temporalidad para considerarla direccional
TF_BIAS_THRESHOLD: Final[float] = 20.0

# Veredictos (score final)
VERDICT_STRONG: Final[float] = 60.0
VERDICT_NORMAL: Final[float] = 25.0

# Límites del multiplicador intermercado
INTERMARKET_MIN_MULT: Final[float] = 0.5
INTERMARKET_MAX_MULT: Final[float] = 1.2
INTERMARKET_REGIME_THRESHOLD: Final[float] = 20.0   # |score| para hablar de régimen claro


# ---------------------------------------------------------------------------
# Riesgo (valores por defecto de la barra lateral)
# ---------------------------------------------------------------------------
DEFAULT_CAPITAL_USD: Final[float] = 10_000.0
DEFAULT_RISK_PCT: Final[float] = 1.0
DEFAULT_SL_ATR_MULT: Final[float] = 1.5
DEFAULT_TP_ATR_MULT: Final[float] = 3.0
MAX_RISK_PCT: Final[float] = 5.0


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
DEFAULT_ALERT_THRESHOLD: Final[float] = 70.0
TELEGRAM_TIMEOUT_S: Final[float] = 10.0
