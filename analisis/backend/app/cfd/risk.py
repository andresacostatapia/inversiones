"""Motor de riesgo del copiloto: calibración del precio de XTB y ticket (entrada, stop, objetivo y lotes).

Calibración: el CFD de XTB no cotiza igual que el dato gratuito (futuro de Yahoo u oro al contado), pero la
diferencia es casi constante durante el día. Se guarda `desfase = precio_xtb − precio_feed` y todos los niveles
se traducen sumándolo. El feed gratuito llega con ~10 minutos de retraso, así que el desfase NO se calcula contra
el último precio del feed (sería de hace 10 minutos), sino contra la vela de 1 minuto del feed que corresponde a
la hora en que miraste xStation: si esa vela todavía no llega, la calibración queda "pendiente" unos minutos.

Lotes: riesgo en USD = capital × riesgo % ; lotes = riesgo / (distancia del stop en puntos × valor del punto),
redondeado HACIA ABAJO al paso de 0,01 (nunca se arriesga más de lo pedido). Si ni el lote mínimo cabe, se dice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .assets import CfdAsset

MAX_GAP = timedelta(minutes=5)       # vela del feed más lejana aceptable para calibrar (p. ej. un minuto sin operaciones)
STALE_CALIBRATION = timedelta(hours=24)


@dataclass
class Calibration:
    asset: str
    xtb_price: float
    at: datetime                                   # hora (UTC) en que se leyó el precio en xStation
    offsets: dict[str, dict] = field(default_factory=dict)  # por fuente: feed_price, offset, bar_time
    manual_offset: float | None = None             # desfase escrito a mano (vale para cualquier fuente)

    def offset_for(self, source: str) -> float | None:
        if self.manual_offset is not None:
            return self.manual_offset
        o = self.offsets.get(source)
        return None if o is None else o["offset"]


def resolve_offset(xtb_price: float, at: datetime, bars_1m: pd.DataFrame) -> dict | None:
    """Desfase contra la vela de 1 minuto del feed que contiene `at`. None = el feed todavía no llega a esa hora.
    Lanza ValueError si el feed no tiene datos cerca de esa hora (mercado cerrado, hora muy antigua)."""
    if bars_1m is None or bars_1m.empty:
        return None
    minute = pd.Timestamp(at).tz_convert("UTC").floor("min")
    last = bars_1m.index[-1]
    if last < minute:
        return None  # el feed con retraso aún no tiene esa vela
    earlier = bars_1m[bars_1m.index <= minute]
    if earlier.empty:
        raise ValueError("el feed no tiene velas de 1 minuto tan antiguas (Yahoo guarda ~7 días): vuelve a calibrar")
    bar_time = earlier.index[-1]
    if minute - bar_time > MAX_GAP:
        raise ValueError("el feed no tiene precio cerca de esa hora (¿mercado cerrado?): calibra con el mercado abierto")
    feed_price = float(earlier["close"].iloc[-1])
    return {"feed_price": feed_price, "offset": xtb_price - feed_price, "bar_time": bar_time.isoformat()}


def _round_down(x: float, step: float) -> float:
    """Redondea hacia abajo al múltiplo de `step` (con tolerancia por errores de coma flotante)."""
    if step <= 0:
        raise ValueError("el paso de lote debe ser mayor que 0")
    n = math.floor(x / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, decimals + 2)


def lot_size(risk_usd: float, sl_points: float, value_per_point: float, step: float = 0.01,
             min_lot: float = 0.01, spread_points: float = 0.0) -> tuple[float, float]:
    """(lotes, riesgo real en USD) para arriesgar como máximo `risk_usd` con un stop a `sl_points` puntos.
    Devuelve lotes 0 si ni el lote mínimo cabe. Lanza ValueError con datos imposibles (división por cero)."""
    per_lot = (sl_points + max(spread_points, 0.0)) * value_per_point
    if not (math.isfinite(per_lot) and per_lot > 0):
        raise ValueError("la distancia del stop y el valor del punto deben ser mayores que 0")
    if not (math.isfinite(risk_usd) and risk_usd > 0):
        raise ValueError("el riesgo en USD debe ser mayor que 0")
    lots = _round_down(risk_usd / per_lot, step)
    if lots + 1e-12 < min_lot:
        return 0.0, 0.0
    return lots, lots * per_lot


@dataclass
class TicketInput:
    direction: int            # +1 largo (compra), −1 corto (venta)
    feed_price: float         # último precio del feed
    atr: float | None         # ATR14 de la temporalidad elegida (en puntos del feed = puntos de XTB)
    offset: float | None      # desfase de calibración (None = sin calibrar)
    capital_usd: float
    risk_pct: float
    sl_atr: float
    tp_atr: float


def build_ticket(t: TicketInput, a: CfdAsset) -> dict:
    """Ticket listo para copiar en xStation. Siempre devuelve un dict; `ok` = False explica por qué no se puede."""
    out: dict = {"ok": False, "asset": a.code, "warnings": [], "calibrated": t.offset is not None,
                 "direction": "LARGO" if t.direction > 0 else "CORTO",
                 "side": "COMPRA (BUY)" if t.direction > 0 else "VENTA (SELL)"}
    if t.direction == 0:
        out["error"] = "Sin dirección: el puntaje es neutral."
        return out
    if t.atr is None or not math.isfinite(t.atr) or t.atr <= 0:
        out["error"] = "No hay ATR para esta temporalidad (faltan velas): no se puede calcular el stop."
        return out
    if not (t.capital_usd > 0):
        out["error"] = "Ingresa un capital mayor que 0 USD."
        return out
    if not (t.risk_pct > 0):
        out["error"] = "Ingresa un riesgo por operación mayor que 0 %."
        return out
    if not (t.sl_atr > 0 and t.tp_atr > 0):
        out["error"] = "Los múltiplos de ATR del stop y del objetivo deben ser mayores que 0."
        return out

    offset = t.offset or 0.0
    entry = t.feed_price + offset
    sl_pts, tp_pts = t.sl_atr * t.atr, t.tp_atr * t.atr
    sl = entry - t.direction * sl_pts
    tp = entry + t.direction * tp_pts
    risk_budget = t.capital_usd * t.risk_pct / 100
    try:
        lots, risk_real = lot_size(risk_budget, sl_pts, a.value_per_point, a.lot_step, a.min_lot, a.spread_points)
    except ValueError as e:
        out["error"] = str(e)
        return out
    d = a.digits
    min_risk = a.min_lot * (sl_pts + a.spread_points) * a.value_per_point
    out.update({
        "entry": round(entry, d), "sl": round(sl, d), "tp": round(tp, d),
        "sl_points": round(sl_pts, d), "tp_points": round(tp_pts, d), "atr": round(t.atr, d),
        "lots": lots, "risk_budget_usd": round(risk_budget, 2), "risk_usd": round(risk_real, 2),
        "risk_pct_real": round(risk_real / t.capital_usd * 100, 3),
        "reward_usd": round(lots * tp_pts * a.value_per_point, 2),
        "rr": round(tp_pts / sl_pts, 2),
        "value_per_point": a.value_per_point, "spread_points": a.spread_points,
        "min_lot": a.min_lot, "min_lot_risk_usd": round(min_risk, 2),
        "notional_usd": round(lots * a.value_per_point * entry, 2),
        "margin_usd": round(lots * a.value_per_point * entry * a.margin_pct / 100, 2),
        "margin_pct": a.margin_pct,
        "usd_per_point": round(lots * a.value_per_point, 2),
    })
    if lots == 0:
        out["error"] = (f"Con {t.capital_usd:,.0f} USD y {t.risk_pct:g} % de riesgo ({risk_budget:,.2f} USD) no alcanza "
                        f"ni el lote mínimo ({a.min_lot:g}): con un stop de {sl_pts:,.{d}f} puntos arriesgaría "
                        f"{min_risk:,.2f} USD ({min_risk / t.capital_usd * 100:.1f} % del capital). "
                        "Usa un stop más corto (menos ATR), una temporalidad menor o no operes.")
        return out
    if out["margin_usd"] > t.capital_usd:
        out["warnings"].append(f"El margen aproximado ({out['margin_usd']:,.2f} USD) supera tu capital: xStation "
                               "rechazaría la orden.")
    if t.offset is None:
        out["warnings"].append("Sin calibrar: los precios son los del feed, no los de xStation. Calibra antes de usarlos.")
    out["ok"] = True
    return out


def calibration_status(cal: Calibration | None, source: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if cal is None:
        return {"state": "sin calibrar", "offset": None, "text": "Sin calibrar: ingresa el precio que ves en xStation."}
    off = cal.offset_for(source)
    age = now - cal.at
    base = {"xtb_price": cal.xtb_price, "at": cal.at.isoformat(), "offset": off,
            "manual": cal.manual_offset is not None, "offsets": cal.offsets}
    if off is None:
        mins = max(0, math.ceil((cal.at + timedelta(minutes=11) - now).total_seconds() / 60))
        text = ("Pendiente: el feed tiene ~10 min de retraso; se completa cuando llegue la vela de esa hora"
                + (f" (≈{mins} min)." if mins else "."))
        return {**base, "state": "pendiente", "text": text}
    if age > STALE_CALIBRATION:
        return {**base, "state": "antigua",
                "text": f"Calibrada hace {age.days} d {age.seconds // 3600} h: recalibra (el desfase cambia con los "
                        "vencimientos del futuro y el swap)."}
    return {**base, "state": "ok", "text": f"Desfase {off:+.2f} puntos"}
