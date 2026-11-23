"""Cartera: posiciones, valorización consolidada en CLP/USD, distribución y reparto de aportes.

Reglas de cálculo:
- **Costo promedio ponderado** por símbolo. Las comisiones de compra suman al costo; las de venta restan del ingreso.
- **Ganancia realizada** de una venta = ingreso neto − costo promedio × cantidad vendida.
- **Dos monedas**: cada operación guarda el dólar (CLP por USD) de su fecha. El costo en CLP de una acción en
  USD usa ese dólar; el valor actual, el dólar de hoy. Por eso la ganancia en CLP de un ETF en USD se separa en
  efecto precio (lo que se movió el activo) y efecto dólar (lo que se movió el tipo de cambio).
- **Caída desde la compra** (para las alertas −20 % / −25 %) = precio actual / costo promedio − 1, en la moneda
  del activo: mide al activo, no al dólar.
- Solo largo: no se puede vender más de lo que se tiene.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

from .providers.base import market_of

BUCKETS = {"CL": "Chile", "INTL": "Internacional"}
EPS = 1e-9


def bucket_of(symbol: str) -> str:
    return "CL" if market_of(symbol) == "CL" else "INTL"


class InvalidTransaction(ValueError):
    pass


@dataclass
class Tx:
    symbol: str
    side: str            # compra / venta
    quantity: float
    price: float
    currency: str
    date: date
    fee: float = 0.0
    fx_rate: float = 1.0  # CLP por USD en la fecha
    id: int | None = None

    def to_clp(self, amount: float) -> float:
        return amount * self.fx_rate if self.currency == "USD" else amount


@dataclass
class Position:
    symbol: str
    currency: str
    quantity: float = 0.0
    cost_native: float = 0.0     # costo de lo que queda, en la moneda del activo (incluye comisiones)
    cost_clp: float = 0.0        # el mismo costo en CLP, con el dólar de cada compra
    cost_usd: float = 0.0
    realized_native: float = 0.0
    realized_clp: float = 0.0
    first_buy: date | None = None
    trades: int = 0

    @property
    def avg_cost(self) -> float | None:
        return self.cost_native / self.quantity if self.quantity > EPS else None


def build_positions(txs: list[Tx]) -> dict[str, Position]:
    """Recorre las operaciones en orden y arma las posiciones. Lanza InvalidTransaction si se vende de más."""
    pos: dict[str, Position] = {}
    for t in sorted(txs, key=lambda t: (t.date, t.side != "compra", t.id or 0)):
        if t.quantity <= 0 or t.price <= 0 or t.fee < 0:
            raise InvalidTransaction(f"{t.symbol} {t.date}: cantidad y precio deben ser positivos")
        p = pos.setdefault(t.symbol, Position(t.symbol, t.currency))
        p.trades += 1
        gross = t.quantity * t.price
        usd = (lambda a: a) if t.currency == "USD" else (lambda a: a / t.fx_rate)
        if t.side == "compra":
            p.quantity += t.quantity
            p.cost_native += gross + t.fee
            p.cost_clp += t.to_clp(gross + t.fee)
            p.cost_usd += usd(gross + t.fee)
            p.first_buy = p.first_buy or t.date
        elif t.side == "venta":
            if t.quantity > p.quantity + EPS:
                raise InvalidTransaction(
                    f"{t.symbol} {t.date}: se venden {t.quantity:g} pero solo hay {p.quantity:g} (no se permiten cortos)"
                )
            frac = t.quantity / p.quantity
            cost_n, cost_c, cost_u = p.cost_native * frac, p.cost_clp * frac, p.cost_usd * frac
            proceeds = gross - t.fee
            p.realized_native += proceeds - cost_n
            p.realized_clp += t.to_clp(proceeds) - cost_c
            p.quantity -= t.quantity
            p.cost_native -= cost_n
            p.cost_clp -= cost_c
            p.cost_usd -= cost_u
            if p.quantity < EPS:
                p.quantity = p.cost_native = p.cost_clp = p.cost_usd = 0.0
                p.first_buy = None
        else:
            raise InvalidTransaction(f"tipo de operación desconocido: {t.side!r}")
    return pos


@dataclass
class PriceInfo:
    price: float | None
    prev_close: float | None = None
    currency: str | None = None
    name: str | None = None
    freshness: dict | None = None


def risk_status(drawdown_pct: float | None, review: float, sell: float) -> str:
    if drawdown_pct is None:
        return "sin_precio"
    drawdown_pct = round(drawdown_pct, 6)  # -19,9999999 por redondeo binario cuenta como -20
    if drawdown_pct <= sell:
        return "vender"
    if drawdown_pct <= review:
        return "revisar"
    return "ok"


def value_portfolio(positions: dict[str, Position], prices: dict[str, PriceInfo], fx_now: float | None,
                    target: dict[str, float], review_pct: float, sell_pct: float) -> dict:
    """Valoriza la cartera en CLP y USD con los precios y el dólar de hoy."""
    rows, closed = [], []
    tot = {"value_clp": 0.0, "cost_clp": 0.0, "day_change_clp": 0.0, "realized_clp": 0.0, "value_usd": 0.0, "cost_usd": 0.0}
    by_bucket = {b: 0.0 for b in BUCKETS}
    missing = []
    for sym, p in sorted(positions.items()):
        tot["realized_clp"] += p.realized_clp
        if p.quantity < EPS:
            closed.append({"symbol": sym, "currency": p.currency, "realized_native": round(p.realized_native, 4),
                           "realized_clp": round(p.realized_clp, 0)})
            continue
        info = prices.get(sym) or PriceInfo(None)
        price = info.price
        fx = fx_now if p.currency == "USD" else 1.0
        row = {
            "symbol": sym, "name": info.name, "market": market_of(sym), "bucket": bucket_of(sym),
            "currency": p.currency, "quantity": p.quantity, "avg_cost": p.avg_cost, "price": price,
            "freshness": info.freshness, "first_buy": p.first_buy.isoformat() if p.first_buy else None,
            "cost_native": p.cost_native, "cost_clp": p.cost_clp, "realized_clp": p.realized_clp,
        }
        if price is None or fx is None:
            missing.append(sym)
            row.update(value_native=None, value_clp=None, unrealized_clp=None, unrealized_pct=None,
                       drawdown_pct=None, price_effect_clp=None, fx_effect_clp=None, day_change_clp=None,
                       weight_pct=None, status="sin_precio")
            rows.append(row)
            continue
        value_native = p.quantity * price
        value_clp = value_native * fx
        unreal_clp = value_clp - p.cost_clp
        drawdown = (price / p.avg_cost - 1) * 100 if p.avg_cost else None
        price_effect = (value_native - p.cost_native) * fx
        day_chg = p.quantity * (price - info.prev_close) * fx if info.prev_close else 0.0
        row.update(
            value_native=value_native, value_clp=value_clp, unrealized_clp=unreal_clp,
            unrealized_pct=unreal_clp / p.cost_clp * 100 if p.cost_clp else None,
            drawdown_pct=drawdown, price_effect_clp=price_effect, fx_effect_clp=unreal_clp - price_effect,
            day_change_clp=day_chg, status=risk_status(drawdown, review_pct, sell_pct),
        )
        rows.append(row)
        tot["value_clp"] += value_clp
        tot["cost_clp"] += p.cost_clp
        tot["day_change_clp"] += day_chg
        tot["cost_usd"] += p.cost_usd
        by_bucket[bucket_of(sym)] += value_clp

    total = tot["value_clp"]
    for r in rows:
        if r["value_clp"] is not None:
            r["weight_pct"] = r["value_clp"] / total * 100 if total else None
    tot["value_usd"] = total / fx_now if fx_now else None
    tot["unrealized_clp"] = total - tot["cost_clp"]
    tot["unrealized_pct"] = tot["unrealized_clp"] / tot["cost_clp"] * 100 if tot["cost_clp"] else None
    allocation = [
        {
            "bucket": b, "name": BUCKETS[b], "value_clp": v,
            "pct": v / total * 100 if total else 0.0,
            "target_pct": target.get(b, 0) * 100,
            "diff_pct": (v / total * 100 if total else 0.0) - target.get(b, 0) * 100,
        }
        for b, v in by_bucket.items()
    ]
    return {
        "positions": rows, "closed": closed, "totals": tot, "allocation": allocation, "fx_now": fx_now,
        "missing_prices": missing,
        "thresholds": {"review_pct": review_pct, "sell_pct": sell_pct},
    }


# --- Reparto del aporte mensual ------------------------------------------------

SIGNAL_WEIGHT = {"COMPRAR": 2.0, "MANTENER": 1.0}  # VENDER o sin señal: no recibe aporte


@dataclass
class Candidate:
    symbol: str
    price_native: float
    currency: str
    price_clp: float
    signal: str | None
    held: bool = False
    name: str | None = None
    risk: str | None = None       # estado de la posición: ok / revisar / vender (las reglas de riesgo mandan)
    drawdown_pct: float | None = None

    @property
    def bucket(self) -> str:
        return bucket_of(self.symbol)


@dataclass
class ContributionLine:
    symbol: str
    name: str | None
    signal: str | None
    quantity: float
    price_native: float
    currency: str
    amount_clp: float
    reason: str


@dataclass
class Contribution:
    amount_clp: float
    buckets: list[dict]
    lines: list[ContributionLine]
    unallocated_clp: float
    excluded: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _qty(amount: float, price: float, fractional: bool) -> float:
    if price <= 0:
        return 0.0
    q = amount / price
    return math.floor(q * 10_000) / 10_000 if fractional else float(math.floor(q + EPS))


def suggest_contribution(amount_clp: float, bucket_values: dict[str, float], candidates: list[Candidate],
                         target: dict[str, float], fractional: dict[str, bool]) -> Contribution:
    """Reparte un aporte en CLP para acercarse a la distribución objetivo. Nunca sugiere vender.

    1. Por mercado: se calcula cuánto le falta a cada uno para su % objetivo DESPUÉS del aporte y el aporte se reparte
       en proporción a esos faltantes (si ninguno falta, según el objetivo).
    2. Dentro de cada mercado: entre los símbolos que tienes o vigilas, sin señal VENDER. COMPRAR pesa el doble que MANTENER.
    3. Se redondea a acciones enteras (o fracciones si tu broker lo permite) y lo que sobra se usa para comprar una
       acción más de lo que quedó más lejos de su monto, mientras alcance.
    """
    if amount_clp <= 0:
        raise ValueError("el aporte debe ser mayor que 0")
    total = sum(bucket_values.values())
    after_total = total + amount_clp
    deficits = {b: max(0.0, after_total * target.get(b, 0) - bucket_values.get(b, 0.0)) for b in BUCKETS}
    sd = sum(deficits.values())
    shares = {b: (deficits[b] / sd if sd > EPS else target.get(b, 0)) for b in BUCKETS}

    def exclusion(c: Candidate) -> str | None:
        # Las reglas de riesgo mandan sobre la señal técnica: no se promedia a la baja una posición en zona de venta.
        if c.risk == "vender":
            return f"caída de {c.drawdown_pct:.1f} % bajo el umbral de venta".replace(".", ",")
        if c.signal == "VENDER":
            return "señal VENDER"
        if c.signal not in SIGNAL_WEIGHT:
            return "sin señal (falta historia)"
        return None

    excluded = [{"symbol": c.symbol, "signal": c.signal, "reason": r} for c in candidates if (r := exclusion(c))]
    eligible = [c for c in candidates if exclusion(c) is None and c.price_clp > 0]

    lines: list[ContributionLine] = []
    buckets_out, unallocated, notes = [], 0.0, []
    spent_by_bucket = {b: 0.0 for b in BUCKETS}
    for b in BUCKETS:
        budget = amount_clp * shares[b]
        pool = [c for c in eligible if c.bucket == b]
        if budget > EPS and not pool:
            notes.append(f"{BUCKETS[b]}: no hay símbolos elegibles (todos en VENDER o sin señal); "
                         f"{round(budget):,} CLP quedan sin asignar".replace(",", "."))
            unallocated += budget
            continue
        wsum = sum(SIGNAL_WEIGHT[c.signal] for c in pool)
        goal = {c.symbol: budget * SIGNAL_WEIGHT[c.signal] / wsum for c in pool} if pool else {}
        qty = {c.symbol: _qty(goal[c.symbol], c.price_clp, fractional.get(market_key(c), False)) for c in pool}
        spent = sum(qty[c.symbol] * c.price_clp for c in pool)
        left = budget - spent
        # Con acciones enteras sobra dinero: una acción más a quien quedó más lejos de su monto, mientras alcance.
        while pool:
            affordable = [c for c in pool if c.price_clp <= left + EPS and not fractional.get(market_key(c), False)]
            if not affordable:
                break
            c = max(affordable, key=lambda c: goal[c.symbol] - qty[c.symbol] * c.price_clp)
            qty[c.symbol] += 1
            left -= c.price_clp
        for c in pool:
            if qty[c.symbol] <= EPS:
                continue
            amt = qty[c.symbol] * c.price_clp
            reason = ("señal COMPRAR (pesa el doble)" if c.signal == "COMPRAR" else "señal MANTENER") + \
                     (" · ya la tienes" if c.held else " · en tu watchlist")
            if c.risk == "revisar":
                reason += f" · ⚠ en revisión ({c.drawdown_pct:.1f} % desde tu compra)".replace(".", ",")
            lines.append(ContributionLine(c.symbol, c.name, c.signal, qty[c.symbol], c.price_native, c.currency,
                                          round(amt, 0), reason))
            spent_by_bucket[b] += amt
        unallocated += max(0.0, left)
        if pool and left > 1 and not any(fractional.get(market_key(c), False) for c in pool):
            cheapest = min(c.price_clp for c in pool)
            if left < cheapest:
                notes.append(f"{BUCKETS[b]}: sobran {round(left):,} CLP porque no alcanzan para otra acción "
                             f"(la más barata cuesta {round(cheapest):,} CLP)".replace(",", "."))

    for b in BUCKETS:
        cur = bucket_values.get(b, 0.0)
        new = cur + spent_by_bucket[b]
        buckets_out.append({
            "bucket": b, "name": BUCKETS[b],
            "current_clp": cur, "current_pct": cur / total * 100 if total else 0.0,
            "target_pct": target.get(b, 0) * 100,
            "amount_clp": round(spent_by_bucket[b], 0),
            "after_pct": new / (total + sum(spent_by_bucket.values())) * 100 if (total + sum(spent_by_bucket.values())) else 0.0,
        })
    notes.append("Solo se reparte dinero nuevo: nunca se sugiere vender para rebalancear.")
    notes.append("No incluye comisiones del broker; los precios pueden venir con retraso.")
    return Contribution(amount_clp, buckets_out, lines, round(unallocated, 0), excluded, notes)


def market_key(c: Candidate) -> str:
    """Clave de config.fractional_shares: CL o US."""
    return "CL" if c.bucket == "CL" else "US"
