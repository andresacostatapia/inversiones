import type { Asset, Currency, Quote, Trade } from '../types'

/** Un monto expresado en ambas monedas. */
export type Money2 = Record<Currency, number>
export const zero = (): Money2 => ({ CLP: 0, USD: 0 })
export const conv = (amount: number, from: Currency, fx: number): Money2 =>
  from === 'CLP' ? { CLP: amount, USD: amount / fx } : { CLP: amount * fx, USD: amount }
const add = (a: Money2, b: Money2, k = 1) => {
  a.CLP += b.CLP * k
  a.USD += b.USD * k
}

export interface Position {
  asset: Asset
  quantity: number
  costNative: number // costo en la moneda en que cotiza el activo (incluye comisiones)
  cost: Money2 // costo convertido con el tipo de cambio de cada compra
  realized: Money2
}

export interface Ledger {
  positions: Position[]
  realizedByTrade: Record<string, Money2>
  realizedTotal: Money2
  invested: Money2 // suma de todas las compras
}

/**
 * Recorre las operaciones en orden cronológico usando costo promedio ponderado.
 * Cada monto se convierte con el tipo de cambio de SU fecha, así la ganancia en
 * CLP de un activo en USD incluye el efecto del dólar.
 */
export function buildLedger(trades: Trade[], assets: Asset[]): Ledger {
  const byId = new Map(assets.map((a) => [a.id, a]))
  const pos = new Map<string, Position>()
  const realizedByTrade: Record<string, Money2> = {}
  const realizedTotal = zero()
  const invested = zero()
  const sorted = [...trades].sort((a, b) => a.date.localeCompare(b.date))

  for (const t of sorted) {
    const asset = byId.get(t.assetId)
    if (!asset) continue
    let p = pos.get(asset.id)
    if (!p) pos.set(asset.id, (p = { asset, quantity: 0, costNative: 0, cost: zero(), realized: zero() }))
    const gross = t.quantity * t.price
    const costs = t.fee + t.tax

    if (t.side === 'compra') {
      const amount = conv(gross + costs, t.currency, t.fxRate)
      p.quantity += t.quantity
      p.costNative += amount[asset.currency]
      add(p.cost, amount)
      add(invested, amount)
    } else {
      const qty = Math.min(t.quantity, p.quantity)
      const share = p.quantity > 0 ? qty / p.quantity : 0
      const proceeds = conv(gross - costs, t.currency, t.fxRate)
      const pnl: Money2 = {
        CLP: proceeds.CLP - p.cost.CLP * share,
        USD: proceeds.USD - p.cost.USD * share,
      }
      p.costNative *= 1 - share
      add(p.cost, p.cost, -share)
      p.quantity -= qty
      add(p.realized, pnl)
      add(realizedTotal, pnl)
      realizedByTrade[t.id] = pnl
    }
  }
  return {
    positions: [...pos.values()].filter((p) => p.quantity > 1e-9),
    realizedByTrade,
    realizedTotal,
    invested,
  }
}

export interface PositionView extends Position {
  price: number
  avgPrice: number
  value: Money2
  pnl: Money2
  pnlPct: Money2
  day: Money2
  dayPct: number
}

export function valuePositions(ledger: Ledger, quotes: Record<string, Quote>, fx: number): PositionView[] {
  return ledger.positions
    .map((p) => {
      const qt = quotes[p.asset.id]
      const price = qt?.price ?? 0
      const prev = qt?.prevClose ?? price
      const value = conv(p.quantity * price, p.asset.currency, fx)
      const pnl = { CLP: value.CLP - p.cost.CLP, USD: value.USD - p.cost.USD }
      return {
        ...p,
        price,
        avgPrice: p.costNative / p.quantity,
        value,
        pnl,
        pnlPct: { CLP: (pnl.CLP / p.cost.CLP) * 100, USD: (pnl.USD / p.cost.USD) * 100 },
        day: conv(p.quantity * (price - prev), p.asset.currency, fx),
        dayPct: prev ? (price / prev - 1) * 100 : 0,
      }
    })
    .sort((a, b) => b.value.CLP - a.value.CLP)
}

export interface Totals {
  value: Money2
  cost: Money2
  day: Money2
  dayPct: Money2
  unrealized: Money2
  sinceStart: Money2 // no realizada + realizada
  sinceStartPct: Money2 // sobre el total invertido
}

export function totals(views: PositionView[], ledger: Ledger): Totals {
  const value = zero()
  const cost = zero()
  const day = zero()
  for (const v of views) {
    add(value, v.value)
    add(cost, v.cost)
    add(day, v.day)
  }
  const each = (f: (c: Currency) => number): Money2 => ({ CLP: f('CLP'), USD: f('USD') })
  const unrealized = each((c) => value[c] - cost[c])
  const sinceStart = each((c) => unrealized[c] + ledger.realizedTotal[c])
  return {
    value,
    cost,
    day,
    dayPct: each((c) => (value[c] - day[c] ? (day[c] / (value[c] - day[c])) * 100 : 0)),
    unrealized,
    sinceStart,
    sinceStartPct: each((c) => (ledger.invested[c] ? (sinceStart[c] / ledger.invested[c]) * 100 : 0)),
  }
}
