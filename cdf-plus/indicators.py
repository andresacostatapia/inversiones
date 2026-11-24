"""
indicators.py — Motor de análisis técnico, filtro intermercado y scoring (-100 a +100).

Todo el cálculo es vectorial con pandas/numpy (sin TA-Lib ni pandas-ta) para evitar
conflictos de dependencias.

Flujo:
  1. `analyze_timeframe`  → sesgo -100..+100 de cada temporalidad (15m, 1h, 4h).
  2. `technical_score`    → combina 1h (60 %), 4h (15 %), 15m (10 %) y alineación MTF (15 %).
  3. `build_intermarket`  → régimen Risk-On / Risk-Off y amplitud entre índices y metales.
  4. `apply_intermarket`  → multiplicador de convicción según confirmaciones/divergencias.
  5. `analyze_market`     → orquesta todo y devuelve un `SignalResult` por activo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from config import ASSETS, INDEX_KEYS, INDICATORS, METAL_KEYS, TF_RULE_WEIGHTS
from data_fetcher import AssetData, MarketSnapshot, frame_is_usable

# Estados de una regla para la interfaz
BULL = "bull"
BEAR = "bear"
NEUTRAL = "neutral"
WARN = "warn"

VERDICT_STRONG_BUY = "COMPRA FUERTE"
VERDICT_BUY = "COMPRA"
VERDICT_NEUTRAL = "NEUTRAL"
VERDICT_SELL = "VENTA"
VERDICT_STRONG_SELL = "VENTA FUERTE"


# ---------------------------------------------------------------------------
# Indicadores (cálculo vectorial)
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    """Media móvil exponencial clásica (alpha = 2 / (n + 1))."""
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder. Gestiona divisiones por cero (sin pérdidas → 100, sin movimiento → 50)."""
    values = close.to_numpy(dtype=float)
    delta = np.diff(values, prepend=values[0])
    gains = pd.Series(np.clip(delta, 0.0, None), index=close.index)
    losses = pd.Series(np.clip(-delta, 0.0, None), index=close.index)
    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().to_numpy()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, np.inf)
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((avg_gain == 0) & (avg_loss == 0), 50.0, out)
    out = np.where(np.isnan(avg_gain) | np.isnan(avg_loss), np.nan, out)
    return pd.Series(out, index=close.index, name="rsi")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD: línea (EMA rápida - EMA lenta), señal (EMA de la línea) e histograma."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=1).mean()
    return line, sig, line - sig


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder (volatilidad real, incluye gaps)."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    prev_close = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    return pd.Series(tr, index=df.index).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve una copia del OHLCV con todas las columnas de indicadores."""
    p = INDICATORS
    out = df.copy()
    close = out["close"]
    out["ema20"] = ema(close, p.ema_fast)
    out["ema50"] = ema(close, p.ema_mid)
    out["ema200"] = ema(close, p.ema_slow)
    out["rsi"] = rsi(close, p.rsi_period)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(close, p.macd_fast, p.macd_slow, p.macd_signal)
    out["atr"] = atr(out, p.atr_period)
    return out


# ---------------------------------------------------------------------------
# Estructuras de resultado
# ---------------------------------------------------------------------------
@dataclass
class RuleResult:
    """Una regla evaluada, lista para mostrarse en el desglose."""

    block: str          # "1H", "4H", "15M", "MTF", "INTERMERCADO"
    name: str
    detail: str
    points: float       # Contribución firmada al score (+ alcista / − bajista)
    max_points: float
    status: str         # BULL / BEAR / NEUTRAL / WARN


@dataclass
class TimeframeAnalysis:
    tf: str
    ok: bool
    score: float = 0.0
    bias: str = "SIN DATOS"
    rules: list[RuleResult] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)
    df: pd.DataFrame | None = None
    bar_time: pd.Timestamp | None = None
    error: str | None = None


@dataclass
class IntermarketContext:
    equity_score: float = 0.0
    metals_score: float = 0.0
    risk_index: float = 0.0
    regime: str = "SIN_DATOS"
    regime_label: str = "Datos insuficientes"
    breadth_aligned: int = 0
    breadth_total: int = 0
    breadth_up: int = 0
    breadth_down: int = 0
    breadth_state: str = "SIN_DATOS"     # SOLIDA / PARCIAL / DEBIL / DIVIDIDA / SIN_TENDENCIA
    breadth_label: str = "—"
    smallcaps_confirm: bool | None = None
    metals_divergent: bool = False
    trends: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.regime != "SIN_DATOS"


@dataclass
class SignalResult:
    key: str
    ok: bool
    score: float = 0.0
    technical_score: float = 0.0
    multiplier: float = 1.0
    verdict: str = VERDICT_NEUTRAL
    direction: int = 0                     # +1 compra, −1 venta, 0 neutral
    timeframes: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    rules: list[RuleResult] = field(default_factory=list)
    intermarket_notes: list[str] = field(default_factory=list)
    error: str | None = None

    def atr(self, tf: str) -> float | None:
        """ATR de una temporalidad (en puntos de índice, sin offset)."""
        t = self.timeframes.get(tf)
        if t is None or not t.ok:
            return None
        value = t.values.get("atr")
        return value if value is not None and np.isfinite(value) and value > 0 else None

    def signal_bar_time(self) -> pd.Timestamp | None:
        """Vela de la temporalidad operativa (para no duplicar alertas)."""
        t = self.timeframes.get(config.OPERATIONAL_TF)
        return t.bar_time if t else None


# ---------------------------------------------------------------------------
# Utilidades de scoring
# ---------------------------------------------------------------------------
def _sign(x: float, threshold: float = 0.0) -> int:
    if x > threshold:
        return 1
    if x < -threshold:
        return -1
    return 0


def _cmp(a: float, b: float) -> int:
    """Compara dos precios con tolerancia relativa: +1 si a > b, −1 si a < b, 0 si empatan."""
    tol = 1e-9 * max(abs(a), abs(b), 1.0)
    return 1 if a - b > tol else -1 if b - a > tol else 0


def bias_label(score: float) -> str:
    s = _sign(score, config.TF_BIAS_THRESHOLD)
    return "ALCISTA" if s > 0 else "BAJISTA" if s < 0 else "LATERAL"


def verdict_for(score: float) -> str:
    if score >= config.VERDICT_STRONG:
        return VERDICT_STRONG_BUY
    if score >= config.VERDICT_NORMAL:
        return VERDICT_BUY
    if score <= -config.VERDICT_STRONG:
        return VERDICT_STRONG_SELL
    if score <= -config.VERDICT_NORMAL:
        return VERDICT_SELL
    return VERDICT_NEUTRAL


def direction_for(score: float) -> int:
    return _sign(score, config.VERDICT_NORMAL - 1e-9)


def _status(points: float) -> str:
    return BULL if points > 0 else BEAR if points < 0 else NEUTRAL


# ---------------------------------------------------------------------------
# 1. Análisis por temporalidad
# ---------------------------------------------------------------------------
def analyze_timeframe(df: pd.DataFrame | None, tf: str) -> TimeframeAnalysis:
    """Evalúa las reglas técnicas en una temporalidad y devuelve su sesgo -100..+100."""
    if not frame_is_usable(df):
        return TimeframeAnalysis(tf=tf, ok=False, error="Datos insuficientes")
    assert df is not None
    data = add_indicators(df)
    last = data.iloc[-1]
    prev = data.iloc[-2]
    p = INDICATORS
    w = TF_RULE_WEIGHTS
    block = tf.upper()
    rules: list[RuleResult] = []

    close = float(last["close"])
    e20, e50, e200 = float(last["ema20"]), float(last["ema50"]), float(last["ema200"])
    r = float(last["rsi"]) if np.isfinite(last["rsi"]) else 50.0
    m, ms = float(last["macd"]), float(last["macd_signal"])
    h, h_prev = float(last["macd_hist"]), float(prev["macd_hist"])

    # Regla 1: tendencia estructural (EMA 200)
    c = _cmp(close, e200)
    pts = w["ema200"] * c
    rules.append(RuleResult(block, "Tendencia EMA 200",
                            f"Precio {'sobre' if c > 0 else 'bajo' if c < 0 else 'en'} EMA 200 ({e200:,.2f})",
                            pts, w["ema200"], _status(pts)))

    # Regla 2: alineación de medias
    c_fast, c_slow = _cmp(e20, e50), _cmp(e50, e200)
    if c_fast > 0 and c_slow > 0:
        pts, txt = w["ema_stack"], "EMA 20 > 50 > 200: tendencia alcista ordenada"
    elif c_fast < 0 and c_slow < 0:
        pts, txt = -w["ema_stack"], "EMA 20 < 50 < 200: tendencia bajista ordenada"
    elif c_fast > 0:
        pts, txt = w["ema_stack"] * 0.4, "EMA 20 > 50 pero bajo EMA 200: rebote / recuperación"
    elif c_fast < 0:
        pts, txt = -w["ema_stack"] * 0.4, "EMA 20 < 50 pero sobre EMA 200: corrección"
    else:
        pts, txt = 0.0, "EMAs entrelazadas: sin estructura de tendencia"
    rules.append(RuleResult(block, "Alineación de EMAs", txt, pts, w["ema_stack"], _status(pts)))

    # Regla 3: cruce MACD
    c = _cmp(m, ms)
    pts = w["macd_cross"] * c
    rules.append(RuleResult(block, "MACD vs señal",
                            f"MACD {m:,.3f} {'>' if c > 0 else '<' if c < 0 else '='} señal {ms:,.3f}",
                            pts, w["macd_cross"], _status(pts)))

    # Regla 4: aceleración del histograma
    slope = _cmp(h, h_prev)
    rising = slope > 0
    if slope == 0:
        pts, txt = 0.0, "Histograma sin cambios: impulso estable"
    elif rising and h > 0:
        pts, txt = w["macd_hist"], "Histograma positivo y creciendo: impulso alcista"
    elif not rising and h < 0:
        pts, txt = -w["macd_hist"], "Histograma negativo y decreciendo: impulso bajista"
    elif rising:
        pts, txt = w["macd_hist"] * 0.5, "Histograma negativo pero recuperando: el impulso bajista se agota"
    else:
        pts, txt = -w["macd_hist"] * 0.5, "Histograma positivo pero cayendo: el impulso alcista se agota"
    rules.append(RuleResult(block, "Histograma MACD", txt, pts, w["macd_hist"], _status(pts)))

    # Regla 5: RSI
    if r > p.rsi_overbought:
        pts, txt, st_ = w["rsi"] * 0.3, f"RSI {r:.1f} en sobrecompra: riesgo de agotamiento", WARN
    elif r < p.rsi_oversold:
        pts, txt, st_ = -w["rsi"] * 0.3, f"RSI {r:.1f} en sobreventa: riesgo de rebote", WARN
    elif r > 50:
        pts, txt, st_ = w["rsi"], f"RSI {r:.1f} en zona de momentum alcista (50–70)", BULL
    elif r < 50:
        pts, txt, st_ = -w["rsi"], f"RSI {r:.1f} en zona de momentum bajista (30–50)", BEAR
    else:
        pts, txt, st_ = 0.0, "RSI en 50: sin momentum", NEUTRAL
    rules.append(RuleResult(block, "RSI (14)", txt, pts, w["rsi"], st_))

    # Regla 6: impulso de corto plazo
    c = _cmp(close, e20)
    pts = w["ema20"] * c
    rules.append(RuleResult(block, "Precio vs EMA 20",
                            f"Precio {'sobre' if c > 0 else 'bajo' if c < 0 else 'en'} EMA 20 ({e20:,.2f})",
                            pts, w["ema20"], _status(pts)))

    score = float(np.clip(sum(x.points for x in rules), -100.0, 100.0))
    values = {
        "close": close, "ema20": e20, "ema50": e50, "ema200": e200, "rsi": r,
        "macd": m, "macd_signal": ms, "macd_hist": h, "atr": float(last["atr"]),
    }
    return TimeframeAnalysis(tf=tf, ok=True, score=score, bias=bias_label(score), rules=rules,
                             values=values, df=data, bar_time=data.index[-1])


# ---------------------------------------------------------------------------
# 2. Score técnico combinado
# ---------------------------------------------------------------------------
def technical_score(tfs: dict[str, TimeframeAnalysis]) -> tuple[float, list[RuleResult]]:
    """Combina las temporalidades en un score técnico -100..+100 con desglose."""
    b = config.SCORE_BLOCKS
    rules: list[RuleResult] = []
    op, hi, lo = tfs.get("1h"), tfs.get("4h"), tfs.get("15m")

    # Bloque operativo: reglas completas de 1h escaladas a su peso
    if op and op.ok:
        factor = b["operational"] / 100.0
        for rr in op.rules:
            rules.append(RuleResult(rr.block, rr.name, rr.detail, rr.points * factor,
                                    rr.max_points * factor, rr.status))
    else:
        rules.append(RuleResult("1H", "Temporalidad operativa", "Sin datos 1h: bloque anulado",
                                0.0, b["operational"], WARN))

    # Temporalidad mayor (contexto)
    if hi and hi.ok:
        pts = hi.score * b["higher_tf"] / 100.0
        rules.append(RuleResult("4H", "Tendencia mayor (4H)",
                                f"Sesgo 4H {hi.bias} ({hi.score:+.0f}/100)", pts, b["higher_tf"], _status(pts)))
    else:
        rules.append(RuleResult("4H", "Tendencia mayor (4H)", "Sin datos 4h", 0.0, b["higher_tf"], WARN))

    # Temporalidad de entrada (timing)
    if lo and lo.ok:
        pts = lo.score * b["timing_tf"] / 100.0
        rules.append(RuleResult("15M", "Timing de entrada (15M)",
                                f"Sesgo 15M {lo.bias} ({lo.score:+.0f}/100)", pts, b["timing_tf"], _status(pts)))
    else:
        rules.append(RuleResult("15M", "Timing de entrada (15M)", "Sin datos 15m", 0.0, b["timing_tf"], WARN))

    # Alineación multi-temporalidad
    th = config.TF_BIAS_THRESHOLD
    signs = {tf: _sign(t.score, th) if t and t.ok else 0 for tf, t in (("15m", lo), ("1h", op), ("4h", hi))}
    if signs["1h"] != 0 and signs["1h"] == signs["4h"] == signs["15m"]:
        pts = b["mtf_align"] * signs["1h"]
        txt = "15M, 1H y 4H alineadas en la misma dirección"
    elif signs["1h"] != 0 and signs["1h"] == signs["4h"]:
        pts = b["mtf_align"] * 0.5 * signs["1h"]
        txt = "1H y 4H alineadas; 15M aún no confirma"
    elif signs["1h"] != 0 and signs["4h"] == -signs["1h"]:
        pts, txt = 0.0, "1H y 4H en conflicto: operar contra la tendencia mayor"
    else:
        pts, txt = 0.0, "Sin alineación clara entre temporalidades"
    status = _status(pts) if pts != 0 else (WARN if "conflicto" in txt else NEUTRAL)
    rules.append(RuleResult("MTF", "Alineación multi-temporalidad", txt, pts, b["mtf_align"], status))

    total = float(np.clip(sum(r.points for r in rules), -100.0, 100.0))
    return total, rules


# ---------------------------------------------------------------------------
# 3. Contexto intermercado
# ---------------------------------------------------------------------------
def _trend_strength(tfs: dict[str, TimeframeAnalysis]) -> float | None:
    """Tendencia de fondo de un activo: media del sesgo 1h y 4h disponibles."""
    vals = [tfs[tf].score for tf in ("1h", "4h") if tf in tfs and tfs[tf].ok]
    return float(np.mean(vals)) if vals else None


def build_intermarket(all_tfs: dict[str, dict[str, TimeframeAnalysis]]) -> IntermarketContext:
    """Evalúa el régimen Risk-On/Risk-Off y la amplitud entre índices USA y metales."""
    ctx = IntermarketContext()
    th = config.INTERMARKET_REGIME_THRESHOLD
    trends: dict[str, float] = {}
    for key, tfs in all_tfs.items():
        t = _trend_strength(tfs)
        if t is not None:
            trends[key] = t
    ctx.trends = trends

    idx = {k: trends[k] for k in INDEX_KEYS if k in trends}
    met = {k: trends[k] for k in METAL_KEYS if k in trends}
    if len(idx) < 2 or not met:
        ctx.notes.append("Filtro intermercado desactivado: faltan datos de índices o metales.")
        return ctx

    ctx.equity_score = float(np.mean(list(idx.values())))
    ctx.metals_score = float(np.mean(list(met.values())))
    ctx.risk_index = (ctx.equity_score - ctx.metals_score) / 2.0

    # Amplitud: cuántos índices acompañan la dirección del bloque
    eq_sign = _sign(ctx.equity_score, th)
    ctx.breadth_total = len(idx)
    ctx.breadth_up = sum(1 for v in idx.values() if v >= th)
    ctx.breadth_down = sum(1 for v in idx.values() if v <= -th)
    ctx.breadth_aligned = ctx.breadth_up if eq_sign > 0 else ctx.breadth_down if eq_sign < 0 else 0
    large = [idx[k] for k in ("US500", "US100") if k in idx]
    if "US2000" in idx and large:
        large_sign = _sign(float(np.mean(large)), th)
        # Solo tiene sentido hablar de confirmación si los grandes índices tienen dirección
        ctx.smallcaps_confirm = None if large_sign == 0 else _sign(idx["US2000"], th) == large_sign
    if ctx.breadth_up > 0 and ctx.breadth_down > 0:
        ctx.breadth_state = "DIVIDIDA"
        ctx.breadth_label = f"Índices divididos (↑{ctx.breadth_up} / ↓{ctx.breadth_down})"
    elif eq_sign != 0 and ctx.breadth_aligned == ctx.breadth_total:
        ctx.breadth_state = "SOLIDA"
        ctx.breadth_label = f"Amplitud sólida {ctx.breadth_aligned}/{ctx.breadth_total}"
    elif eq_sign != 0 and ctx.breadth_aligned <= ctx.breadth_total // 2:
        ctx.breadth_state = "DEBIL"
        ctx.breadth_label = f"Amplitud débil {ctx.breadth_aligned}/{ctx.breadth_total}"
    elif eq_sign != 0:
        ctx.breadth_state = "PARCIAL"
        ctx.breadth_label = f"Amplitud parcial {ctx.breadth_aligned}/{ctx.breadth_total}"
    else:
        ctx.breadth_state = "SIN_TENDENCIA"
        ctx.breadth_label = "Índices sin tendencia clara"
    if ctx.smallcaps_confirm is False:
        ctx.breadth_label += " · Russell no confirma"

    # Divergencia entre metales
    if "GOLD" in met and "SILVER" in met:
        g, s = _sign(met["GOLD"], th), _sign(met["SILVER"], th)
        ctx.metals_divergent = g != 0 and s != 0 and g != s

    # Régimen
    if ctx.equity_score >= th and ctx.metals_score >= th:
        ctx.regime, ctx.regime_label = "LIQUIDEZ", "Liquidez generalizada (índices y metales al alza)"
    elif ctx.equity_score <= -th and ctx.metals_score <= -th:
        ctx.regime, ctx.regime_label = "DESAPALANCAMIENTO", "Desapalancamiento (venta generalizada)"
    elif ctx.risk_index >= th:
        ctx.regime, ctx.regime_label = "RISK_ON", "RISK-ON (apetito por riesgo)"
    elif ctx.risk_index <= -th:
        ctx.regime, ctx.regime_label = "RISK_OFF", "RISK-OFF (flujo hacia refugio)"
    else:
        ctx.regime, ctx.regime_label = "MIXTO", "Régimen mixto / sin dirección clara"
    ctx.notes.append(
        f"Índices {ctx.equity_score:+.0f} · Metales {ctx.metals_score:+.0f} · Risk index {ctx.risk_index:+.0f}"
    )
    return ctx


# ---------------------------------------------------------------------------
# 4. Ajuste de convicción
# ---------------------------------------------------------------------------
def intermarket_multiplier(key: str, direction: int, ctx: IntermarketContext) -> tuple[float, list[str]]:
    """Multiplicador de convicción de un activo según el contexto intermercado."""
    if direction == 0 or not ctx.available:
        return 1.0, (["Sin dirección técnica: el filtro intermercado no aplica."] if ctx.available else ctx.notes[:1])

    th = config.INTERMARKET_REGIME_THRESHOLD
    mult = 1.0
    notes: list[str] = []
    d = direction
    word = "compra" if d > 0 else "venta"
    asset = ASSETS[key]

    def adj(factor: float, text: str) -> None:
        nonlocal mult
        mult *= factor
        notes.append(f"{text} (×{factor:.2f})")

    if asset.category == "index":
        regime_map = {
            "RISK_ON": (1.10, 0.70),
            "RISK_OFF": (0.70, 1.10),
            "LIQUIDEZ": (1.00, 0.85),
            "DESAPALANCAMIENTO": (0.80, 1.05),
            "MIXTO": (0.95, 0.95),
        }
        f_buy, f_sell = regime_map[ctx.regime]
        f = f_buy if d > 0 else f_sell
        if f != 1.0:
            verb = "confirma" if f > 1 else "penaliza"
            adj(f, f"{ctx.regime_label} {verb} la {word} en índices")
        else:
            notes.append(f"{ctx.regime_label}: neutral para la {word}")

        eq_sign = _sign(ctx.equity_score, th)
        if ctx.breadth_state == "SOLIDA" and eq_sign == d:
            adj(1.05, f"Amplitud {ctx.breadth_aligned}/{ctx.breadth_total}: todos los índices acompañan")
        elif ctx.breadth_state == "DIVIDIDA":
            adj(0.85, f"Índices divididos (↑{ctx.breadth_up} / ↓{ctx.breadth_down})")
        elif ctx.breadth_state == "DEBIL" and eq_sign == d:
            adj(0.92, f"Amplitud débil ({ctx.breadth_aligned}/{ctx.breadth_total} índices acompañan)")

        if ctx.smallcaps_confirm is False:
            if key == "US2000":
                adj(0.85, "Russell 2000 diverge de los grandes índices")
            elif eq_sign == d:
                adj(0.92, "Small caps (US2000) no confirman: amplitud estrecha")

        own = ctx.trends.get(key)
        if own is not None and eq_sign != 0 and _sign(own, th) == -eq_sign:
            adj(0.85, f"{key} va contra el bloque de índices")
    else:
        regime_map = {
            "RISK_ON": (0.85, 1.05),
            "RISK_OFF": (1.10, 0.75),
            "LIQUIDEZ": (1.00, 0.90),
            "DESAPALANCAMIENTO": (0.85, 1.05),
            "MIXTO": (0.95, 0.95),
        }
        f_buy, f_sell = regime_map[ctx.regime]
        f = f_buy if d > 0 else f_sell
        if f != 1.0:
            verb = "confirma" if f > 1 else "penaliza"
            adj(f, f"{ctx.regime_label} {verb} la {word} en metales")
        else:
            notes.append(f"{ctx.regime_label}: neutral para la {word} en metales")

        if ctx.metals_divergent:
            adj(0.85, "Oro y plata divergen: flujo refugio poco fiable")
        else:
            g, s = ctx.trends.get("GOLD"), ctx.trends.get("SILVER")
            if g is not None and s is not None and _sign(g, th) == _sign(s, th) == d:
                adj(1.05, "Oro y plata confirman la misma dirección")

    mult = float(np.clip(mult, config.INTERMARKET_MIN_MULT, config.INTERMARKET_MAX_MULT))
    return mult, notes


# ---------------------------------------------------------------------------
# 5. Orquestación
# ---------------------------------------------------------------------------
def analyze_asset_timeframes(data: AssetData) -> dict[str, TimeframeAnalysis]:
    """Analiza las 3 temporalidades de un activo."""
    if not data.ok:
        return {}
    return {tf: analyze_timeframe(data.frames.get(tf), tf) for tf in config.TIMEFRAMES}


def analyze_market(snapshot: MarketSnapshot) -> tuple[dict[str, SignalResult], IntermarketContext]:
    """Calcula señales de los 6 activos aplicando el filtro intermercado."""
    all_tfs: dict[str, dict[str, TimeframeAnalysis]] = {}
    for key, data in snapshot.assets.items():
        try:
            all_tfs[key] = analyze_asset_timeframes(data)
        except Exception as exc:  # Nunca romper el escáner por un activo
            all_tfs[key] = {}
            data.warnings.append(f"Error de análisis: {exc}")

    ctx = build_intermarket(all_tfs)
    results: dict[str, SignalResult] = {}
    for key, data in snapshot.assets.items():
        tfs = all_tfs.get(key, {})
        if not data.ok or not any(t.ok for t in tfs.values()):
            results[key] = SignalResult(key=key, ok=False, timeframes=tfs,
                                        error=data.error or "Sin datos analizables")
            continue
        tech, rules = technical_score(tfs)
        mult, notes = intermarket_multiplier(key, _sign(tech), ctx)
        final = float(np.clip(tech * mult, -100.0, 100.0))
        delta = final - tech
        rules.append(RuleResult(
            "INTERMERCADO", "Filtro Risk-On / Risk-Off",
            f"Multiplicador ×{mult:.2f} · {ctx.regime_label}",
            delta, abs(tech) * (config.INTERMARKET_MAX_MULT - 1.0) if tech else 0.0,
            # Verde/rojo si refuerza la señal en su dirección; aviso si la debilita
            _status(tech) if abs(final) > abs(tech) else WARN if abs(final) < abs(tech) else NEUTRAL,
        ))
        results[key] = SignalResult(
            key=key, ok=True, score=final, technical_score=tech, multiplier=mult,
            verdict=verdict_for(final), direction=direction_for(final),
            timeframes=tfs, rules=rules, intermarket_notes=notes,
        )
    return results, ctx
