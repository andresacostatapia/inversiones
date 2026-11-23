"""Motor de señales: combina las estrategias en un puntaje, una etiqueta, una confianza y sus razones.

Puntaje combinado = promedio ponderado de las estrategias que tienen datos ese día
                    × factor de volumen (confirma o debilita), acotado a ±100.
Etiqueta: >= umbral de compra → COMPRAR; <= umbral de venta → VENDER; en medio → MANTENER.

Confianza (0–100 %) = (65 % acuerdo entre estrategias + 35 % fuerza del puntaje) × calidad de los datos.
- Acuerdo: peso de las estrategias que apuntan en la dirección de la señal (las neutrales cuentan la mitad).
  Para MANTENER: peso de las estrategias que también están en zona neutral.
- Fuerza: cuánto pasa el puntaje del umbral (o, para MANTENER, cuán cerca está de 0).
- Calidad: baja si faltan estrategias por falta de historia, si el precio está desactualizado
  o si la volatilidad (ATR) está muy por sobre lo normal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import SignalsConfig
from .strategies import STRATEGIES
from .textfmt import num, pct

BUY, HOLD, SELL = "COMPRAR", "MANTENER", "VENDER"
NEUTRAL_BAND = 10          # |puntaje| < 10: la estrategia se considera neutral
HIGH_RV, LOW_RV = 1.5, 0.7  # volumen relativo alto / bajo
HIGH_VOL_RATIO = 1.5       # ATR% sobre 1,5 veces su mediana de un año = volatilidad alta
MIN_WEIGHT = 0.5           # sin datos para al menos la mitad del peso, no hay señal

ACTIONS = {
    BUY: {"holding": "Puedes mantener o aumentar la posición.", "not_holding": "Considerar comprar."},
    HOLD: {"holding": "Mantener.", "not_holding": "Esperar una mejor entrada."},
    SELL: {"holding": "Considerar vender o reducir la posición.", "not_holding": "Evitar comprar por ahora."},
}


def active_weights(cfg: SignalsConfig) -> dict[str, float]:
    return {k: float(w) for k, w in cfg.weights.items() if k in STRATEGIES and w > 0}


def label_for(score: float | None, cfg: SignalsConfig) -> str | None:
    if score is None or pd.isna(score):
        return None
    if score >= cfg.thresholds.buy:
        return BUY
    if score <= cfg.thresholds.sell:
        return SELL
    return HOLD


def score_frame(df: pd.DataFrame, cfg: SignalsConfig, provisional_last: bool = False) -> pd.DataFrame:
    """Puntajes de cada estrategia y combinado para todos los días.

    `df` debe traer los indicadores (indicators.compute_all). Solo usa datos hasta cada día: sin mirar el futuro.
    Devuelve columnas `<estrategia>` (puntaje), auxiliares de cada estrategia, `raw` (promedio ponderado),
    `volume_factor`, `composite`, `available_weight` y `vol_ratio`.
    """
    weights = active_weights(cfg)
    out = pd.DataFrame(index=df.index)
    num_ = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for key, w in weights.items():
        res = STRATEGIES[key].compute(df)
        out[key] = res["score"]
        for col in res.columns.drop("score"):
            out[col] = res[col]
        has = res["score"].notna()
        num_ += res["score"].fillna(0) * w
        den += has * w
    total = sum(weights.values()) or 1.0
    raw = (num_ / den).where(den >= MIN_WEIGHT * total)
    out["raw"] = raw
    out["available_weight"] = den / total

    # Volumen: confirma si acompaña al movimiento del día en la dirección de la señal; si no, la debilita.
    rv = df["rel_volume"]
    day_ret = df["close"].pct_change()
    eff = cfg.volume_effect
    same_dir = np.sign(day_ret) == np.sign(raw)
    factor = np.where(
        (rv >= HIGH_RV) & same_dir & (raw != 0), 1 + eff,
        np.where((rv >= HIGH_RV) & ~same_dir & (raw != 0), 1 - eff, np.where(rv < LOW_RV, 1 - eff / 2, 1.0)),
    )
    factor = pd.Series(factor, index=df.index).where(rv.notna(), 1.0)
    if provisional_last and len(factor):
        factor.iloc[-1] = 1.0  # el volumen del día en curso está incompleto: no se usa
    out["volume_factor"] = factor
    out["rel_volume"] = rv
    out["day_ret"] = day_ret
    out["composite"] = (raw * factor).clip(-100, 100)

    atr_pct = df["atr14_pct"]
    out["vol_ratio"] = atr_pct / atr_pct.rolling(252, min_periods=60).median()
    return out


@dataclass
class StrategyView:
    key: str
    label: str
    score: float | None
    weight: float
    reasons: list[str]


@dataclass
class SignalResult:
    symbol: str
    date: str
    label: str | None
    score: float | None
    confidence: float | None
    provisional: bool
    summary: str
    reasons: list[str]
    strategies: list[StrategyView]
    volume_factor: float
    actions: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    stop_reference: float | None = None
    thresholds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _confidence(row: pd.Series, label: str, weights: dict[str, float], cfg: SignalsConfig,
                stale: bool) -> tuple[float, list[str]]:
    warnings = []
    total = sum(weights.values()) or 1.0
    scores = {k: row.get(k) for k in weights}
    avail = {k: s for k, s in scores.items() if pd.notna(s)}
    c = float(row["composite"])
    buy, sell = cfg.thresholds.buy, cfg.thresholds.sell

    if label == HOLD:
        agree = sum(weights[k] for k, s in avail.items() if sell < s < buy)
        strength = 1 - min(1.0, abs(c) / (buy if c >= 0 else -sell))
    else:
        d = 1 if label == BUY else -1
        agree = sum(weights[k] * (1.0 if np.sign(s) == d and abs(s) >= NEUTRAL_BAND else
                                   0.5 if abs(s) < NEUTRAL_BAND else 0.0) for k, s in avail.items())
        thr = buy if d == 1 else -sell
        strength = min(1.0, (abs(c) - thr) / max(1e-9, 100 - thr))
    agree /= sum(weights[k] for k in avail) or 1.0

    quality = sum(weights[k] for k in avail) / total
    if quality < 1:
        warnings.append("Algunas estrategias no tienen historia suficiente: confianza reducida")
    if stale:
        quality *= 0.85
        warnings.append("El precio está desactualizado: confianza reducida")
    vr = row.get("vol_ratio")
    if pd.notna(vr) and vr >= HIGH_VOL_RATIO:
        quality *= 0.8
        warnings.append(f"Volatilidad alta (ATR {num(vr, 1)} veces lo normal): confianza reducida")
    conf = 100 * (0.65 * agree + 0.35 * max(0.0, strength)) * quality
    return round(conf, 1), warnings


def _volume_reason(row: pd.Series, provisional: bool) -> str | None:
    rv, f = row.get("rel_volume"), row.get("volume_factor", 1.0)
    if pd.isna(rv):
        return None
    if provisional:
        return f"Volumen del día aún incompleto ({num(rv, 1)}x hasta ahora): no se usa"
    if f > 1:
        return f"Volumen {num(rv, 1)}x el promedio confirma el movimiento"
    if f < 1 and rv >= HIGH_RV:
        return f"Volumen {num(rv, 1)}x el promedio va contra la señal: la debilita"
    if f < 1:
        return f"Volumen bajo ({num(rv, 1)}x el promedio): poca convicción"
    return None


def evaluate(symbol: str, df_ind: pd.DataFrame, cfg: SignalsConfig, *, provisional: bool = False,
             stale: bool = False, frame: pd.DataFrame | None = None) -> SignalResult:
    """Señal del último día de `df_ind` (indicadores ya calculados)."""
    weights = active_weights(cfg)
    if frame is None:
        frame = score_frame(df_ind, cfg, provisional_last=provisional)
    date = df_ind.index[-1].date().isoformat() if len(df_ind) else ""
    if not len(frame) or pd.isna(frame["composite"].iloc[-1]):
        return SignalResult(symbol, date, None, None, None, provisional,
                            "Sin datos suficientes para generar una señal", [], [], 1.0, {},
                            ["Se necesitan al menos 20 días de historia (y 200 para todas las estrategias)"], None, cfg.thresholds.model_dump())
    row = pd.concat([df_ind.iloc[-1], frame.iloc[-1]])
    row = row[~row.index.duplicated(keep="last")]
    score = float(row["composite"])
    label = label_for(score, cfg)
    conf, warnings = _confidence(row, label, weights, cfg, stale)

    views = []
    for key, w in weights.items():
        s = row.get(key)
        views.append(StrategyView(key, STRATEGIES[key].label, None if pd.isna(s) else round(float(s), 1), w,
                                  STRATEGIES[key].explain(row)))

    # Resumen: la primera razón de las estrategias que más empujan hacia la señal.
    if label == HOLD:
        lead = sorted([v for v in views if v.score is not None], key=lambda v: abs(v.score), reverse=True)[:2]
    else:
        d = 1 if label == BUY else -1
        lead = sorted([v for v in views if v.score is not None and np.sign(v.score) == d],
                      key=lambda v: abs(v.score) * v.weight, reverse=True)[:2]
    if label == HOLD and len(lead) == 2 and np.sign(lead[0].score) != np.sign(lead[1].score):
        summary = f"Señales mixtas: {lead[0].reasons[0]} vs. {lead[1].reasons[0]}"
    else:
        summary = " + ".join(v.reasons[0] for v in lead) if lead else "Señales mixtas"

    reasons = [r for v in views for r in v.reasons]
    vr = _volume_reason(row, provisional)
    if vr:
        reasons.append(vr)
    if provisional:
        warnings.insert(0, "Señal provisional: el mercado está abierto y la vela de hoy puede cambiar hasta el cierre")

    stop = None
    if pd.notna(row.get("atr14")):
        stop = round(float(row["close"] - 2 * row["atr14"]), 4)
        reasons.append(f"Stop de referencia: {num(stop)} (cierre − 2×ATR, {pct(-2 * row['atr14_pct'])})")

    return SignalResult(
        symbol=symbol, date=date, label=label, score=round(score, 1), confidence=conf, provisional=provisional,
        summary=summary, reasons=reasons, strategies=views, volume_factor=round(float(row["volume_factor"]), 3),
        actions=ACTIONS[label], warnings=warnings, stop_reference=stop, thresholds=cfg.thresholds.model_dump(),
    )


def signal_for_bars(symbol: str, bars: pd.DataFrame, cfg: SignalsConfig, **kw) -> SignalResult:
    return evaluate(symbol, ind.compute_all(bars), cfg, **kw)


def score_history(bars: pd.DataFrame, cfg: SignalsConfig) -> pd.DataFrame:
    """Serie diaria de puntajes y etiqueta (para graficar cómo evolucionó la señal)."""
    df = ind.compute_all(bars)
    frame = score_frame(df, cfg)
    cols = [*active_weights(cfg), "composite"]
    out = frame[cols].copy()
    out["label"] = [label_for(v, cfg) for v in out["composite"]]
    return out
