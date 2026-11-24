"""Activos CFD de XTB, temporalidades y especificación de contrato.

Valor por punto (USD por 1,0 lote y por 1 punto de precio) según la tabla de especificaciones de XTB:
    GOLD 100 (1 lote = 100 oz)   SILVER 5000 (5000 oz)   US500 50   US100 20   US30 5   US2000 200
Lote mínimo y paso: 0,01. Se pueden corregir en config.yaml (sección cfd.assets) si XTB los cambia.

Margen: porcentaje aproximado del nocional exigido a un cliente minorista (apalancamiento ESMA: índices principales
y oro 1:20 = 5 %; plata y Russell 2000 1:10 = 10 %). Solo es una referencia: revisa el margen real en xStation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from pydantic import BaseModel, Field

# Temporalidades de menor a mayor. "4h" se arma con velas de 1 hora (Yahoo no entrega 4 horas).
TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h", "4h", "1d"]
OPERABLE: list[str] = ["1m", "5m", "15m", "1h", "4h"]  # temporalidades en que se puede "operar" (la de 1d es contexto)
TF_DELTA: dict[str, timedelta] = {
    "1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1),
}
TF_LABEL: dict[str, str] = {"1m": "1 min", "5m": "5 min", "15m": "15 min", "1h": "1 hora", "4h": "4 horas", "1d": "1 día"}


@dataclass(frozen=True)
class CfdAsset:
    code: str                    # nombre del CFD en xStation
    name: str
    group: str                   # "indice" o "metal"
    yahoo: str                   # símbolo de respaldo en Yahoo (futuro continuo, casi 24 h)
    twelvedata: str | None       # símbolo en Twelve Data (si se configuró una API key); None = solo Yahoo
    value_per_point: float       # USD por punto y por 1,0 lote
    min_lot: float = 0.01
    lot_step: float = 0.01
    digits: int = 2              # decimales del precio en xStation
    margin_pct: float = 5.0      # margen aproximado en % del nocional
    spread_points: float = 0.0   # spread típico en puntos (opcional; se suma al riesgo)


DEFAULT_ASSETS: dict[str, CfdAsset] = {
    a.code: a
    for a in (
        CfdAsset("GOLD", "Oro (onza)", "metal", "GC=F", "XAU/USD", 100.0, digits=2, margin_pct=5.0),
        CfdAsset("SILVER", "Plata (onza)", "metal", "SI=F", "XAG/USD", 5000.0, digits=3, margin_pct=10.0),
        CfdAsset("US500", "S&P 500", "indice", "ES=F", None, 50.0, digits=1, margin_pct=5.0),
        CfdAsset("US100", "Nasdaq 100", "indice", "NQ=F", None, 20.0, digits=1, margin_pct=5.0),
        CfdAsset("US30", "Dow Jones 30", "indice", "YM=F", None, 5.0, digits=1, margin_pct=5.0),
        CfdAsset("US2000", "Russell 2000", "indice", "RTY=F", None, 200.0, digits=1, margin_pct=10.0),
    )
}
INDICES = ["US500", "US100", "US30", "US2000"]
METALS = ["GOLD", "SILVER"]


class AssetOverride(BaseModel):
    yahoo: str | None = None
    twelvedata: str | None = None
    value_per_point: float | None = Field(None, gt=0)
    min_lot: float | None = Field(None, gt=0)
    lot_step: float | None = Field(None, gt=0)
    digits: int | None = Field(None, ge=0, le=6)
    margin_pct: float | None = Field(None, gt=0, le=100)
    spread_points: float | None = Field(None, ge=0)


class CfdConfig(BaseModel):
    """Sección `cfd` de config.yaml (valores de partida; lo que se cambia en la pestaña CFD se guarda en la base)."""

    enabled: bool = True
    assets: dict[str, AssetOverride] = Field(default_factory=dict)
    use_twelvedata: bool = True        # si hay TWELVEDATA_API_KEY en .env, usarla para los símbolos que la tengan
    twelvedata_per_minute: int = 6     # cupo propio del copiloto (el plan gratis tiene 8/min y 800/día en total)
    twelvedata_per_day: int = 500
    # Temporalidades que se piden a Twelve Data (donde el retraso de Yahoo más molesta); el resto, a Yahoo.
    twelvedata_timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m"])
    yahoo_per_minute: int = 40
    scan_seconds: int = 60             # cada cuánto se revisan las alertas con el mercado abierto
    alert_channels: list[str] = Field(default_factory=lambda: ["telegram"])


def build_assets(cfg: CfdConfig) -> dict[str, CfdAsset]:
    out = {}
    for code, a in DEFAULT_ASSETS.items():
        ov = cfg.assets.get(code)
        out[code] = replace(a, **{k: v for k, v in ov.model_dump().items() if v is not None}) if ov else a
        if ov and "twelvedata" in ov.model_fields_set and ov.twelvedata is None:
            out[code] = replace(out[code], twelvedata=None)  # `twelvedata: null` en el yaml lo desactiva
    return out


def higher(tf: str, steps: int = 1) -> str | None:
    i = TIMEFRAMES.index(tf) + steps
    return TIMEFRAMES[i] if i < len(TIMEFRAMES) else None
