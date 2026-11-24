"""
risk_engine.py — Calculadora de riesgo especializada en CFDs de XTB.

Incluye:
  * Calibrador de offset: offset = precio_xtb − precio_feed. Traduce Entrada/SL/TP del
    feed de datos al precio exacto que muestra xStation. Los offsets se guardan en disco
    (.state/offsets.json) para que sobrevivan a reinicios de la app.
  * Cálculo dinámico de lotaje: lotes = riesgo_USD / (distancia_SL × USD_por_punto_por_lote),
    redondeado HACIA ABAJO a 0.01 para no superar nunca el riesgo máximo.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from config import AssetConfig

logger = logging.getLogger("cdfplus.risk")


# ---------------------------------------------------------------------------
# Calibrador de offset XTB
# ---------------------------------------------------------------------------
@dataclass
class OffsetCalibration:
    """Calibración guardada de un activo."""

    key: str
    xtb_price: float
    feed_price: float
    offset: float
    provider: str
    feed_symbol: str
    timestamp: str  # ISO 8601 UTC

    @property
    def age_hours(self) -> float:
        try:
            ts = datetime.fromisoformat(self.timestamp)
        except ValueError:
            return float("inf")
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def compute_offset(xtb_price: float, feed_price: float) -> float:
    """offset = precio_xtb − precio_feed (validando que ambos sean precios reales)."""
    if not (math.isfinite(xtb_price) and math.isfinite(feed_price)) or xtb_price <= 0 or feed_price <= 0:
        raise ValueError("Los precios para calibrar deben ser números positivos")
    return xtb_price - feed_price


def to_xtb_price(feed_price: float, offset: float) -> float:
    """Convierte un precio del feed (ya escalado) al precio equivalente de xStation."""
    return feed_price + offset


class OffsetStore:
    """Persistencia thread-safe de los offsets por activo en un JSON local."""

    def __init__(self, path: Path = config.OFFSETS_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, OffsetCalibration] = self._load()

    def _load(self) -> dict[str, OffsetCalibration]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: OffsetCalibration(**v) for k, v in raw.items()}
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("No se pudieron leer los offsets (%s); se empieza de cero", exc)
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: asdict(v) for k, v in self._data.items()}, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("No se pudieron guardar los offsets: %s", exc)

    def get(self, key: str, provider: str, feed_symbol: str) -> OffsetCalibration | None:
        """Devuelve la calibración solo si corresponde al mismo feed (proveedor + símbolo)."""
        cal = self._data.get(key)
        if cal and cal.provider == provider and cal.feed_symbol == feed_symbol:
            return cal
        return None

    def calibrate(self, key: str, xtb_price: float, feed_price: float,
                  provider: str, feed_symbol: str) -> OffsetCalibration:
        cal = OffsetCalibration(
            key=key, xtb_price=float(xtb_price), feed_price=float(feed_price),
            offset=compute_offset(xtb_price, feed_price), provider=provider,
            feed_symbol=feed_symbol, timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._data[key] = cal
            self._save()
        return cal

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key, None)
            self._save()

    def all(self) -> dict[str, OffsetCalibration]:
        return dict(self._data)


# ---------------------------------------------------------------------------
# Lotaje
# ---------------------------------------------------------------------------
@dataclass
class RiskSettings:
    capital_usd: float = config.DEFAULT_CAPITAL_USD
    risk_pct: float = config.DEFAULT_RISK_PCT
    sl_atr_mult: float = config.DEFAULT_SL_ATR_MULT
    tp_atr_mult: float = config.DEFAULT_TP_ATR_MULT

    @property
    def risk_usd(self) -> float:
        return max(0.0, self.capital_usd) * max(0.0, self.risk_pct) / 100.0

    @property
    def rr_ratio(self) -> float:
        return self.tp_atr_mult / self.sl_atr_mult if self.sl_atr_mult > 0 else 0.0


@dataclass
class LotResult:
    lots: float
    raw_lots: float
    risk_usd_real: float
    warnings: list[str] = field(default_factory=list)


def floor_to_step(value: float, step: float) -> float:
    """Redondea hacia abajo al múltiplo de `step` (tolerante a errores de coma flotante)."""
    if step <= 0:
        raise ValueError("El incremento de lote debe ser positivo")
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(value / step + 1e-9) * step, decimals)


def calculate_lot_size(risk_usd: float, sl_distance: float, point_value_usd: float,
                       min_lot: float = 0.01, lot_step: float = 0.01,
                       max_lot: float = 100.0) -> LotResult:
    """
    lotes = riesgo_USD / (distancia_SL_en_puntos × USD_por_punto_por_lote)

    Protege contra divisiones por cero y valores no finitos. Si el riesgo no alcanza para
    el lote mínimo, devuelve 0 lotes y explica cuánto se arriesgaría con el mínimo.
    """
    warnings: list[str] = []
    if not all(math.isfinite(x) for x in (risk_usd, sl_distance, point_value_usd)):
        return LotResult(0.0, 0.0, 0.0, ["Valores no numéricos en el cálculo de lotaje"])
    if risk_usd <= 0:
        return LotResult(0.0, 0.0, 0.0, ["El riesgo en USD es 0: revisa capital y % de riesgo"])
    if sl_distance <= 0:
        return LotResult(0.0, 0.0, 0.0, ["Distancia al Stop Loss nula (ATR = 0): no se puede dimensionar"])
    if point_value_usd <= 0:
        return LotResult(0.0, 0.0, 0.0, ["Valor del punto inválido en la configuración del activo"])

    risk_per_lot = sl_distance * point_value_usd
    raw = risk_usd / risk_per_lot
    lots = floor_to_step(raw, lot_step)

    if lots > max_lot:
        warnings.append(f"Lotaje limitado al máximo configurado ({max_lot:.2f})")
        lots = max_lot
    if lots < min_lot:
        min_risk = min_lot * risk_per_lot
        warnings.append(
            f"El riesgo permitido (${risk_usd:,.2f}) no alcanza para el lote mínimo de {min_lot:.2f}: "
            f"con {min_lot:.2f} lotes arriesgarías ${min_risk:,.2f}. Amplía capital/riesgo o espera menos volatilidad."
        )
        return LotResult(0.0, raw, 0.0, warnings)
    return LotResult(lots, raw, lots * risk_per_lot, warnings)


# ---------------------------------------------------------------------------
# Ticket listo para XTB
# ---------------------------------------------------------------------------
@dataclass
class TradeTicket:
    key: str
    direction: int                       # +1 compra, −1 venta, 0 sin dirección
    digits: int
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    sl_distance: float = 0.0             # En puntos de precio
    tp_distance: float = 0.0
    atr: float = 0.0
    lots: float = 0.0
    raw_lots: float = 0.0
    risk_usd_target: float = 0.0
    risk_usd_real: float = 0.0
    reward_usd: float = 0.0
    rr_ratio: float = 0.0
    nominal_usd: float = 0.0
    margin_usd: float = 0.0
    offset: float = 0.0
    calibrated: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Niveles calculables (aunque el volumen pueda ser 0)."""
        return not self.errors and self.direction != 0

    @property
    def executable(self) -> bool:
        """Listo para introducir en xStation."""
        return self.valid and self.lots > 0

    @property
    def side_label(self) -> str:
        return "COMPRA (BUY)" if self.direction > 0 else "VENTA (SELL)" if self.direction < 0 else "SIN SEÑAL"

    def fmt(self, price: float) -> str:
        return f"{price:.{self.digits}f}"


def build_ticket(asset: AssetConfig, direction: int, feed_price: float | None, atr_value: float | None,
                 offset: float, settings: RiskSettings, calibrated: bool,
                 point_value_usd: float | None = None) -> TradeTicket:
    """
    Construye el ticket de la orden en precios de xStation.

    feed_price y atr_value deben estar en puntos de índice (ya escalados por data_fetcher).
    """
    pv = point_value_usd if point_value_usd is not None else asset.point_value_usd
    t = TradeTicket(key=asset.key, direction=direction, digits=asset.digits, offset=offset,
                    calibrated=calibrated, risk_usd_target=settings.risk_usd)

    blocking: list[str] = []
    if feed_price is None or not math.isfinite(feed_price) or feed_price <= 0:
        blocking.append("Sin precio válido del feed")
    if atr_value is None or not math.isfinite(atr_value) or atr_value <= 0:
        blocking.append("ATR no disponible o igual a 0 (¿mercado sin movimiento?)")
    if settings.sl_atr_mult <= 0 or settings.tp_atr_mult <= 0:
        blocking.append("Los multiplicadores de ATR deben ser mayores que 0")
    if direction == 0:
        t.errors.append("Score en zona NEUTRAL: no hay operación sugerida (puedes forzar una dirección).")
    if blocking or feed_price is None or atr_value is None:
        t.errors.extend(blocking)
        return t

    t.atr = atr_value
    entry = to_xtb_price(feed_price, offset)
    d = direction if direction != 0 else 1   # Para mostrar niveles orientativos si es neutral
    sl = entry - d * settings.sl_atr_mult * atr_value
    tp = entry + d * settings.tp_atr_mult * atr_value

    # Redondeo al número de decimales de xStation y distancias reales tras redondear
    t.entry, t.stop_loss, t.take_profit = (round(x, asset.digits) for x in (entry, sl, tp))
    t.sl_distance = abs(t.entry - t.stop_loss)
    t.tp_distance = abs(t.take_profit - t.entry)
    if t.stop_loss <= 0:
        t.errors.append("El Stop Loss calculado queda en precio negativo: reduce el multiplicador de ATR")
        return t

    lot = calculate_lot_size(settings.risk_usd, t.sl_distance, pv,
                             asset.min_lot, asset.lot_step, asset.max_lot)
    t.lots, t.raw_lots, t.risk_usd_real = lot.lots, lot.raw_lots, lot.risk_usd_real
    t.warnings.extend(lot.warnings)
    t.reward_usd = t.lots * t.tp_distance * pv
    t.rr_ratio = t.tp_distance / t.sl_distance if t.sl_distance > 0 else 0.0
    t.nominal_usd = t.lots * t.entry * pv
    t.margin_usd = t.nominal_usd * asset.margin_rate

    if not calibrated:
        t.warnings.append(
            "Activo SIN calibrar: los niveles están en precio del feed, no de xStation. "
            "Introduce el precio actual de xStation en el calibrador."
        )
    if settings.capital_usd > 0 and t.margin_usd > settings.capital_usd:
        t.warnings.append("El margen estimado supera el capital de la cuenta")
    return t
