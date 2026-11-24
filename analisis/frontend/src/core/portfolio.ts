// Cartera: misma lógica que backend/app/portfolio.py (costo promedio, valorización CLP/USD, reparto de aportes).

import { pyRound, toFixedPy } from './fmt'
import { marketOf } from './market'

export const BUCKETS: Record<'CL' | 'INTL', string> = { CL: 'Chile', INTL: 'Internacional' }
const EPS = 1e-9

export const bucketOf = (symbol: string): 'CL' | 'INTL' => (marketOf(symbol) === 'CL' ? 'CL' : 'INTL')

export class InvalidTransaction extends Error {}

const SUPPORTED_CURRENCIES = ['CLP', 'USD']
// ETF apalancados o inversos, detectados por el nombre (misma expresión que portfolio.py).
const LEVERAGE_RE = /\b[2-5](?:[.,]\d)?x\b|\bultra(?:pro|short)?\b|leveraged|\binverse\b|\b-[1-5]x\b|\bshort (?:s&p|qqq|dow|russell|nasdaq)/i

/** Si el perfil permite COMPRAR el instrumento: null = sí; un texto = por qué no (igual que investability en Python). */
export function investability(symbol: string, kind?: string | null, currency?: string | null, name?: string | null,
  allowDerivatives = false, allowLeverage = false, allowShort = false): string | null {
  const s = symbol.toUpperCase()
  const k = (kind ?? '').toUpperCase()
  if (k === 'INDEX' || s.startsWith('^')) {
    return 'Es un índice: no se compra directamente. Usa un ETF que lo replique '
      + '(p. ej. SPY, VOO o IVV para el S&P 500; QQQ para el Nasdaq 100).'
  }
  if ((k === 'FUTURE' || k === 'OPTION' || s.endsWith('=F')) && !allowDerivatives) {
    return 'Es un futuro (derivado) y tu perfil no permite derivados. Para oro, plata, cobre o petróleo '
      + 'usa un ETF: GLD o IAU, SLV, CPER, USO.'
  }
  if (k === 'CURRENCY' || s.endsWith('=X')) return 'Es un tipo de cambio: solo sirve para análisis.'
  if (currency && !SUPPORTED_CURRENCIES.includes(currency.toUpperCase())) {
    return `Cotiza en ${currency.toUpperCase()}: la cartera por ahora solo valoriza en CLP y USD.`
  }
  if (name && LEVERAGE_RE.test(name) && !(allowLeverage && allowShort)) {
    return 'Parece un ETF apalancado o inverso: tu perfil no permite apalancamiento ni posiciones cortas.'
  }
  return null
}

export interface Tx {
  symbol: string
  side: 'compra' | 'venta'
  quantity: number
  price: number
  currency: string
  date: string // AAAA-MM-DD
  fee: number
  fx_rate: number // CLP por USD en la fecha
  id?: number | null
}

export interface Position {
  symbol: string
  currency: string
  quantity: number
  cost_native: number
  cost_clp: number
  cost_usd: number
  realized_native: number
  realized_clp: number
  first_buy: string | null
  trades: number
}

const avgCost = (p: Position) => (p.quantity > EPS ? p.cost_native / p.quantity : null)

export function buildPositions(txs: Tx[]): Record<string, Position> {
  const pos: Record<string, Position> = {}
  const sorted = [...txs].sort((a, b) =>
    a.date < b.date ? -1 : a.date > b.date ? 1
      : (a.side !== 'compra' ? 1 : 0) - (b.side !== 'compra' ? 1 : 0) || (a.id ?? 0) - (b.id ?? 0))
  for (const t of sorted) {
    if (t.quantity <= 0 || t.price <= 0 || t.fee < 0) throw new InvalidTransaction(`${t.symbol} ${t.date}: cantidad y precio deben ser positivos`)
    const p = (pos[t.symbol] ??= { symbol: t.symbol, currency: t.currency, quantity: 0, cost_native: 0, cost_clp: 0,
      cost_usd: 0, realized_native: 0, realized_clp: 0, first_buy: null, trades: 0 })
    p.trades++
    const gross = t.quantity * t.price
    const toClp = (a: number) => (t.currency === 'USD' ? a * t.fx_rate : a)
    const toUsd = (a: number) => (t.currency === 'USD' ? a : a / t.fx_rate)
    if (t.side === 'compra') {
      p.quantity += t.quantity
      p.cost_native += gross + t.fee
      p.cost_clp += toClp(gross + t.fee)
      p.cost_usd += toUsd(gross + t.fee)
      p.first_buy ??= t.date
    } else if (t.side === 'venta') {
      if (t.quantity > p.quantity + EPS) {
        throw new InvalidTransaction(`${t.symbol} ${t.date}: se venden ${fmtG(t.quantity)} pero solo hay ${fmtG(p.quantity)} (no se permiten cortos)`)
      }
      const frac = t.quantity / p.quantity
      const cn = p.cost_native * frac
      const cc = p.cost_clp * frac
      const cu = p.cost_usd * frac
      const proceeds = gross - t.fee
      p.realized_native += proceeds - cn
      p.realized_clp += toClp(proceeds) - cc
      p.quantity -= t.quantity
      p.cost_native -= cn
      p.cost_clp -= cc
      p.cost_usd -= cu
      if (p.quantity < EPS) {
        p.quantity = p.cost_native = p.cost_clp = p.cost_usd = 0
        p.first_buy = null
      }
    } else {
      throw new InvalidTransaction(`tipo de operación desconocido: ${String((t as Tx).side)}`)
    }
  }
  return pos
}

/** Formato "%g" de Python para cantidades (10 → "10", 2.5 → "2.5"). */
function fmtG(v: number): string {
  return String(Number(v.toPrecision(6)))
}

export interface PriceInfo {
  price: number | null
  prev_close?: number | null
  currency?: string | null
  name?: string | null
  freshness?: unknown
}

export type RiskStatus = 'ok' | 'revisar' | 'vender' | 'sin_precio'

export function riskStatus(dd: number | null, review: number, sell: number): RiskStatus {
  if (dd === null) return 'sin_precio'
  const d = pyRound(dd, 6)
  if (d <= sell) return 'vender'
  if (d <= review) return 'revisar'
  return 'ok'
}

export function valuePortfolio(positions: Record<string, Position>, prices: Record<string, PriceInfo>, fxNow: number | null,
  target: Record<string, number>, reviewPct: number, sellPct: number) {
  const rows: Record<string, unknown>[] = []
  const closed: Record<string, unknown>[] = []
  const tot = { value_clp: 0, cost_clp: 0, day_change_clp: 0, realized_clp: 0, value_usd: 0 as number | null, cost_usd: 0,
    unrealized_clp: 0, unrealized_pct: null as number | null }
  const byBucket: Record<string, number> = { CL: 0, INTL: 0 }
  const missing: string[] = []
  for (const sym of Object.keys(positions).sort()) {
    const p = positions[sym]
    tot.realized_clp += p.realized_clp
    if (p.quantity < EPS) {
      closed.push({ symbol: sym, currency: p.currency, realized_native: pyRound(p.realized_native, 4), realized_clp: pyRound(p.realized_clp, 0) })
      continue
    }
    const info = prices[sym] ?? { price: null }
    const price = info.price ?? null
    const fx = p.currency === 'USD' ? fxNow : 1
    const row: Record<string, unknown> = {
      symbol: sym, name: info.name ?? null, market: marketOf(sym), bucket: bucketOf(sym), currency: p.currency,
      quantity: p.quantity, avg_cost: avgCost(p), price, freshness: info.freshness ?? null, first_buy: p.first_buy,
      cost_native: p.cost_native, cost_clp: p.cost_clp, realized_clp: p.realized_clp,
    }
    if (price === null || fx === null) {
      missing.push(sym)
      Object.assign(row, { value_native: null, value_clp: null, unrealized_clp: null, unrealized_pct: null, drawdown_pct: null,
        price_effect_clp: null, fx_effect_clp: null, day_change_clp: null, weight_pct: null, status: 'sin_precio' })
      rows.push(row)
      continue
    }
    const valueNative = p.quantity * price
    const valueClp = valueNative * fx
    const unreal = valueClp - p.cost_clp
    const ac = avgCost(p)
    const dd = ac ? (price / ac - 1) * 100 : null
    const priceEffect = (valueNative - p.cost_native) * fx
    const dayChg = info.prev_close ? p.quantity * (price - info.prev_close) * fx : 0
    Object.assign(row, {
      value_native: valueNative, value_clp: valueClp, unrealized_clp: unreal,
      unrealized_pct: p.cost_clp ? (unreal / p.cost_clp) * 100 : null, drawdown_pct: dd,
      price_effect_clp: priceEffect, fx_effect_clp: unreal - priceEffect, day_change_clp: dayChg,
      status: riskStatus(dd, reviewPct, sellPct),
    })
    rows.push(row)
    tot.value_clp += valueClp
    tot.cost_clp += p.cost_clp
    tot.day_change_clp += dayChg
    tot.cost_usd += p.cost_usd
    byBucket[bucketOf(sym)] += valueClp
  }
  const total = tot.value_clp
  for (const r of rows) if (r.value_clp !== null) r.weight_pct = total ? ((r.value_clp as number) / total) * 100 : null
  tot.value_usd = fxNow ? total / fxNow : null
  tot.unrealized_clp = total - tot.cost_clp
  tot.unrealized_pct = tot.cost_clp ? (tot.unrealized_clp / tot.cost_clp) * 100 : null
  const allocation = (['CL', 'INTL'] as const).map((b) => {
    const v = byBucket[b]
    const p = total ? (v / total) * 100 : 0
    return { bucket: b, name: BUCKETS[b], value_clp: v, pct: p, target_pct: (target[b] ?? 0) * 100, diff_pct: p - (target[b] ?? 0) * 100 }
  })
  return { positions: rows, closed, totals: tot, allocation, fx_now: fxNow, missing_prices: missing,
    thresholds: { review_pct: reviewPct, sell_pct: sellPct } }
}

// --- Reparto del aporte ---------------------------------------------------------------

const SIGNAL_WEIGHT: Record<string, number> = { COMPRAR: 2, MANTENER: 1 }

export interface Candidate {
  symbol: string
  price_native: number
  currency: string
  price_clp: number
  signal: string | null
  held?: boolean
  name?: string | null
  risk?: string | null
  drawdown_pct?: number | null
  blocked?: string | null // motivo por el que el perfil no permite comprarlo
}

export interface ContributionLine {
  symbol: string
  name: string | null
  signal: string | null
  quantity: number
  price_native: number
  currency: string
  amount_clp: number
  reason: string
}

const marketKey = (c: Candidate) => (bucketOf(c.symbol) === 'CL' ? 'CL' : 'US')

function qtyFor(amount: number, price: number, fractional: boolean): number {
  if (price <= 0) return 0
  const q = amount / price
  return fractional ? Math.floor(q * 10_000) / 10_000 : Math.floor(q + EPS)
}

const thousands = (v: number) => String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
const comma1 = (v: number) => toFixedPy(v, 1).replace('.', ',')

export function suggestContribution(amountClp: number, bucketValues: Record<string, number>, candidates: Candidate[],
  target: Record<string, number>, fractional: Record<string, boolean>) {
  if (amountClp <= 0) throw new Error('el aporte debe ser mayor que 0')
  const total = Object.values(bucketValues).reduce((a, b) => a + b, 0)
  const after = total + amountClp
  const deficits: Record<string, number> = {}
  for (const b of ['CL', 'INTL']) deficits[b] = Math.max(0, after * (target[b] ?? 0) - (bucketValues[b] ?? 0))
  const sd = deficits.CL + deficits.INTL
  const shares: Record<string, number> = {}
  for (const b of ['CL', 'INTL']) shares[b] = sd > EPS ? deficits[b] / sd : target[b] ?? 0

  const exclusion = (c: Candidate): string | null => {
    if (c.blocked) return c.blocked
    if (c.risk === 'vender') return `caída de ${comma1(c.drawdown_pct ?? 0)} % bajo el umbral de venta`
    if (c.signal === 'VENDER') return 'señal VENDER'
    if (!c.signal || !(c.signal in SIGNAL_WEIGHT)) return 'sin señal (falta historia)'
    return null
  }
  const excluded = candidates.flatMap((c) => {
    const r = exclusion(c)
    return r ? [{ symbol: c.symbol, signal: c.signal, reason: r }] : []
  })
  const eligible = candidates.filter((c) => exclusion(c) === null && c.price_clp > 0)

  const lines: ContributionLine[] = []
  const notes: string[] = []
  let unallocated = 0
  const spent: Record<string, number> = { CL: 0, INTL: 0 }
  for (const b of ['CL', 'INTL'] as const) {
    const budget = amountClp * shares[b]
    const pool = eligible.filter((c) => bucketOf(c.symbol) === b)
    if (budget > EPS && !pool.length) {
      notes.push(`${BUCKETS[b]}: no hay símbolos elegibles (todos en VENDER o sin señal); ${thousands(budget)} CLP quedan sin asignar`)
      unallocated += budget
      continue
    }
    const wsum = pool.reduce((a, c) => a + SIGNAL_WEIGHT[c.signal!], 0)
    const goal = Object.fromEntries(pool.map((c) => [c.symbol, (budget * SIGNAL_WEIGHT[c.signal!]) / wsum]))
    const qty = Object.fromEntries(pool.map((c) => [c.symbol, qtyFor(goal[c.symbol], c.price_clp, !!fractional[marketKey(c)])]))
    let left = budget - pool.reduce((a, c) => a + qty[c.symbol] * c.price_clp, 0)
    for (;;) {
      const affordable = pool.filter((c) => c.price_clp <= left + EPS && !fractional[marketKey(c)])
      if (!affordable.length) break
      let best = affordable[0]
      for (const c of affordable.slice(1)) {
        if (goal[c.symbol] - qty[c.symbol] * c.price_clp > goal[best.symbol] - qty[best.symbol] * best.price_clp) best = c
      }
      qty[best.symbol] += 1
      left -= best.price_clp
    }
    for (const c of pool) {
      if (qty[c.symbol] <= EPS) continue
      const amt = qty[c.symbol] * c.price_clp
      let reason = (c.signal === 'COMPRAR' ? 'señal COMPRAR (pesa el doble)' : 'señal MANTENER') + (c.held ? ' · ya la tienes' : ' · en tu watchlist')
      if (c.risk === 'revisar') reason += ` · ⚠ en revisión (${comma1(c.drawdown_pct ?? 0)} % desde tu compra)`
      lines.push({ symbol: c.symbol, name: c.name ?? null, signal: c.signal, quantity: qty[c.symbol], price_native: c.price_native,
        currency: c.currency, amount_clp: pyRound(amt, 0), reason })
      spent[b] += amt
    }
    unallocated += Math.max(0, left)
    if (pool.length && left > 1 && !pool.some((c) => fractional[marketKey(c)])) {
      const cheapest = Math.min(...pool.map((c) => c.price_clp))
      if (left < cheapest) notes.push(`${BUCKETS[b]}: sobran ${thousands(left)} CLP porque no alcanzan para otra acción (la más barata cuesta ${thousands(cheapest)} CLP)`)
    }
  }
  const spentAll = spent.CL + spent.INTL
  const buckets = (['CL', 'INTL'] as const).map((b) => {
    const cur = bucketValues[b] ?? 0
    return {
      bucket: b, name: BUCKETS[b], current_clp: cur, current_pct: total ? (cur / total) * 100 : 0,
      target_pct: (target[b] ?? 0) * 100, amount_clp: pyRound(spent[b], 0),
      after_pct: total + spentAll ? ((cur + spent[b]) / (total + spentAll)) * 100 : 0,
    }
  })
  notes.push('Solo se reparte dinero nuevo: nunca se sugiere vender para rebalancear.')
  notes.push('No incluye comisiones del broker; los precios pueden venir con retraso.')
  return { amount_clp: amountClp, buckets, lines, unallocated_clp: pyRound(unallocated, 0), excluded, notes }
}
