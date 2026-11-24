"""Puntaje técnico del copiloto CFD: de −100 (venta fuerte) a +100 (compra fuerte), con el detalle de cada regla.

1. Por temporalidad (solo velas CERRADAS, para que la señal no cambie mientras la vela se forma):
   - Tendencia: precio vs EMA200 (peso 2), EMA20 vs EMA50 (1,5), EMA50 vs EMA200 (1).
   - Momento: RSI14 (1): 55-70 alcista, 30-45 bajista, 45-55 neutro; sobre 70 o bajo 30 resta (no perseguir).
   - MACD 12/26/9: línea sobre/bajo la señal (1) y el histograma creciendo o cayendo (0,5).
   Puntaje de la temporalidad = 100 × Σ(peso × valor) / Σ(pesos), con cada valor entre −1 y +1.
2. Varias temporalidades: la elegida pesa 50 %, la siguiente mayor 30 % y la otra 20 % (p. ej. 5m + 15m + 1h).
3. Filtro intermercado (se suma o resta, ver `intermarket`): apetito por riesgo de los 4 índices frente al oro y la
   plata, y la amplitud del mercado (si el Russell 2000 acompaña al S&P 500).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import pandas as pd

from .. import indicators as ind
from .assets import INDICES, METALS, TF_DELTA, TF_LABEL, TIMEFRAMES, higher

MIN_BARS = 35            # menos que esto: no hay MACD confiable y no se puntúa la temporalidad
MTF_WEIGHTS = (0.5, 0.3, 0.2)


@dataclass
class Rule:
    id: str
    group: str               # "Tendencia 5 min", "Momento 15 min", "Intermercado"…
    label: str
    detail: str
    value: float             # −1..+1 (o puntos directos en las de intermercado)
    weight: float = 0.0
    points: float = 0.0      # aporte al puntaje final, en puntos
    available: bool = True

    @property
    def direction(self) -> str:
        return "alcista" if self.value > 0 else "bajista" if self.value < 0 else "neutral"

    def to_dict(self) -> dict:
        return {"id": self.id, "group": self.group, "label": self.label, "detail": self.detail,
                "value": round(self.value, 3), "weight": self.weight, "points": round(self.points, 1),
                "direction": self.direction, "available": self.available}


@dataclass
class TfAnalysis:
    tf: str
    score: float | None
    rules: list[Rule] = field(default_factory=list)
    last: dict = field(default_factory=dict)       # último valor de los indicadores (vela cerrada)
    candle_time: pd.Timestamp | None = None         # inicio de la última vela cerrada
    bars: int = 0
    reason: str | None = None                       # por qué no hay puntaje

    @property
    def bias(self) -> str:
        if self.score is None:
            return "sin datos"
        return "alcista" if self.score >= 20 else "bajista" if self.score <= -20 else "neutral"


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def with_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    c = df["close"]
    for n in (20, 50, 200):
        df[f"ema{n}"] = ind.ema(c, n)
    df["rsi14"] = ind.rsi(c, 14)
    df = df.join(ind.macd(c))
    df["atr14"] = ind.atr(df["high"], df["low"], c, 14)
    return df


def closed_bars(bars: pd.DataFrame, tf: str, asof: pd.Timestamp) -> pd.DataFrame:
    """Quita la vela en formación: la que todavía no termina según la hora del dato (`asof`)."""
    if bars.empty:
        return bars
    end = bars.index + TF_DELTA[tf]
    return bars[end <= asof]


def analyze_tf(bars: pd.DataFrame, tf: str, asof: pd.Timestamp) -> TfAnalysis:
    closed = closed_bars(bars, tf, asof)
    if len(closed) < MIN_BARS:
        return TfAnalysis(tf, None, bars=len(closed), reason=f"solo {len(closed)} velas cerradas (se necesitan {MIN_BARS})")
    df = with_indicators(closed)
    row, prev = df.iloc[-1], df.iloc[-2]
    close, e20, e50, e200 = (_f(row[k]) for k in ("close", "ema20", "ema50", "ema200"))
    rsi, macd, sig = _f(row["rsi14"]), _f(row["macd"]), _f(row["macd_signal"])
    hist, hist_prev = _f(row["macd_hist"]), _f(prev["macd_hist"])
    lbl = TF_LABEL[tf]
    rules: list[Rule] = []

    def add(rid, group, label, detail, value, weight, ok=True):
        rules.append(Rule(f"{tf}:{rid}", f"{group} {lbl}", label, detail, value if ok else 0.0, weight, available=ok))

    ok = e200 is not None
    add("p_ema200", "Tendencia", "Precio sobre la EMA200" if ok and close > e200 else "Precio bajo la EMA200",
        f"{close:.2f} vs {e200:.2f}" if ok else f"sin EMA200 (faltan velas: hay {len(df)})",
        (1.0 if close > e200 else -1.0) if ok else 0.0, 2.0, ok)
    ok = e20 is not None and e50 is not None
    add("ema20_50", "Tendencia", "EMA20 sobre la EMA50" if ok and e20 > e50 else "EMA20 bajo la EMA50",
        f"{e20:.2f} vs {e50:.2f}" if ok else "sin datos", (1.0 if e20 > e50 else -1.0) if ok else 0.0, 1.5, ok)
    ok = e50 is not None and e200 is not None
    add("ema50_200", "Tendencia", "EMA50 sobre la EMA200" if ok and e50 > e200 else "EMA50 bajo la EMA200",
        f"{e50:.2f} vs {e200:.2f}" if ok else "sin EMA200", (1.0 if e50 > e200 else -1.0) if ok else 0.0, 1.0, ok)

    if rsi is not None:
        if rsi >= 70:
            v, txt = -0.5, "RSI en sobrecompra (≥70): no perseguir el alza"
        elif rsi >= 55:
            v, txt = 1.0, "RSI con momento alcista (55-70)"
        elif rsi > 45:
            v, txt = 0.0, "RSI neutro (45-55)"
        elif rsi > 30:
            v, txt = -1.0, "RSI con momento bajista (30-45)"
        else:
            v, txt = 0.5, "RSI en sobreventa (≤30): no perseguir la baja"
        add("rsi", "Momento", txt, f"RSI14 = {rsi:.1f}", v, 1.0)
    else:
        add("rsi", "Momento", "RSI14", "sin datos", 0.0, 1.0, False)

    ok = macd is not None and sig is not None
    add("macd", "Momento", "MACD sobre su señal" if ok and macd > sig else "MACD bajo su señal",
        f"{macd:.3f} vs {sig:.3f}" if ok else "sin datos", (1.0 if macd > sig else -1.0) if ok else 0.0, 1.0, ok)
    ok = hist is not None and hist_prev is not None
    add("macd_hist", "Momento", "Histograma MACD creciendo" if ok and hist >= hist_prev else "Histograma MACD cayendo",
        f"{hist:.3f} (antes {hist_prev:.3f})" if ok else "sin datos",
        (0.5 if hist > hist_prev else -0.5 if hist < hist_prev else 0.0) if ok else 0.0, 1.0, ok)

    wsum = sum(r.weight for r in rules if r.available)
    score = 100 * sum(r.weight * r.value for r in rules if r.available) / wsum if wsum else None
    last = {k: _f(row[k]) for k in ("close", "ema20", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14")}
    return TfAnalysis(tf, score, rules, last, closed.index[-1], len(closed))


def mtf_plan(tf: str) -> list[tuple[str, float]]:
    """Temporalidades que componen el puntaje de `tf` y su peso: la elegida y las dos siguientes mayores."""
    out = []
    for i, w in enumerate(MTF_WEIGHTS):
        t = higher(tf, i)
        if t is not None:
            out.append((t, w))
    return out


def regime_tf(tf: str) -> str:
    """Temporalidad del filtro intermercado: al menos 1 hora (el "clima" del mercado no cambia minuto a minuto)."""
    return TIMEFRAMES[max(TIMEFRAMES.index(tf) + 1, TIMEFRAMES.index("1h"))]


def composite(by_tf: dict[str, TfAnalysis], tf: str) -> tuple[float | None, list[Rule]]:
    """Puntaje combinado de varias temporalidades. Las que no tienen datos no cuentan (se reparten los pesos)."""
    plan = [(t, w) for t, w in mtf_plan(tf) if by_tf.get(t) is not None and by_tf[t].score is not None]
    if not plan or plan[0][0] != tf:
        return None, []  # sin la temporalidad elegida no hay señal
    total_w = sum(w for _, w in plan)
    rules: list[Rule] = []
    score = 0.0
    for t, w in plan:
        a = by_tf[t]
        share = w / total_w
        wsum = sum(r.weight for r in a.rules if r.available)
        for r in a.rules:  # copia: el mismo análisis sirve para varias temporalidades elegidas
            pts = 100 * share * r.weight * r.value / wsum if (r.available and wsum) else 0.0
            rules.append(replace(r, points=pts))
        score += share * a.score
    return score, rules


@dataclass
class Regime:
    tf: str
    state: str                    # "risk-on" / "risk-off" / "mixto" / "sin datos"
    label: str
    risk_score: float | None      # promedio de los 4 índices
    metals_score: float | None    # promedio de oro y plata
    breadth: str                  # "amplia" / "débil" / "sin datos"
    detail: str
    scores: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"tf": self.tf, "state": self.state, "label": self.label, "risk_score": r(self.risk_score),
                "metals_score": r(self.metals_score), "breadth": self.breadth, "detail": self.detail,
                "scores": {k: r(v) for k, v in self.scores.items()}}


def _mean(vals) -> float | None:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def regime(scores: dict[str, float | None], tf: str) -> Regime:
    """Clima del mercado según los puntajes (de una temporalidad mayor) de los 6 activos."""
    risk = _mean(scores.get(c) for c in INDICES)
    metals = _mean(scores.get(c) for c in METALS)
    spx, rut = scores.get("US500"), scores.get("US2000")
    if risk is None:
        state, label = "sin datos", "Sin datos de los índices"
    elif risk >= 20 and (metals is None or risk > metals):
        state, label = "risk-on", "Apetito por riesgo (risk-on): índices al alza"
    elif risk <= -20 or (metals is not None and metals >= 20 and risk < 0):
        state, label = "risk-off", "Aversión al riesgo (risk-off): índices a la baja"
    else:
        state, label = "mixto", "Mercado mixto: sin dirección clara entre índices y metales"
    if spx is None or rut is None:
        breadth = "sin datos"
    elif (spx >= 20 and rut < 0) or (spx <= -20 and rut > 0):
        breadth = "débil"
    else:
        breadth = "amplia"
    fmt = lambda v: "—" if v is None else f"{v:+.0f}"  # noqa: E731
    detail = (f"Índices {fmt(risk)} · metales {fmt(metals)} · S&P 500 {fmt(spx)} vs Russell 2000 {fmt(rut)} "
              f"({TF_LABEL[tf]})")
    return Regime(tf, state, label, risk, metals, breadth, detail, dict(scores))


def intermarket(code: str, base: float, rg: Regime) -> list[Rule]:
    """Ajustes intermercado al puntaje de un activo (puntos directos, en total hasta ±15)."""
    group = f"Intermercado {TF_LABEL[rg.tf]}"
    out: list[Rule] = []
    if rg.state == "sin datos" or base == 0:
        return out
    is_index = code in INDICES
    if is_index:
        if rg.state == "risk-on":
            out.append(Rule("im:regime", group, "Clima risk-on favorece a los índices", rg.detail, 10, points=10))
        elif rg.state == "risk-off":
            out.append(Rule("im:regime", group, "Clima risk-off pesa sobre los índices", rg.detail, -10, points=-10))
        if rg.breadth == "débil":
            # Un alza (o baja) que el Russell 2000 no acompaña es menos confiable: se modera el puntaje.
            if base > 0 and (rg.scores.get("US500") or 0) > 0:
                out.append(Rule("im:breadth", group, "Amplitud débil: el Russell 2000 no acompaña el alza",
                                rg.detail, -5, points=-5))
            elif base < 0 and (rg.scores.get("US500") or 0) < 0:
                out.append(Rule("im:breadth", group, "Amplitud débil: el Russell 2000 no acompaña la baja",
                                rg.detail, 5, points=5))
        elif rg.breadth == "amplia" and rg.state in ("risk-on", "risk-off"):
            p = 5 if rg.state == "risk-on" else -5
            out.append(Rule("im:breadth", group, "Amplitud amplia: el Russell 2000 confirma", rg.detail, p, points=p))
    else:
        if rg.state == "risk-off":
            out.append(Rule("im:regime", group, "Clima risk-off: demanda de refugio (metales)", rg.detail, 10, points=10))
        elif rg.state == "risk-on":
            out.append(Rule("im:regime", group, "Clima risk-on: menos demanda de refugio", rg.detail, -5, points=-5))
        other = "SILVER" if code == "GOLD" else "GOLD"
        o = rg.scores.get(other)
        if o is not None and abs(o) >= 20:
            same = (o > 0) == (base > 0)
            p = (5 if base > 0 else -5) if same else (-5 if base > 0 else 5)
            name = "La plata" if other == "SILVER" else "El oro"
            out.append(Rule("im:metal", group, f"{name} {'confirma' if same else 'no confirma'} la dirección",
                            f"{other} {o:+.0f}", p, points=p))
    return out


def verdict(score: float | None, strong: float = 70, normal: float = 30) -> str:
    if score is None:
        return "SIN DATOS"
    if score >= strong:
        return "COMPRA FUERTE"
    if score >= normal:
        return "COMPRA"
    if score <= -strong:
        return "VENTA FUERTE"
    if score <= -normal:
        return "VENTA"
    return "NEUTRAL"


def clip(v: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))
