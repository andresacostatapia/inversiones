// Yahoo Finance (API de gráficos v8): precio actual e historia diaria. Mismas reglas que el proveedor de Python:
// el cierre anterior se toma de la propia serie y se descartan las velas de relleno (volumen 0, precio plano).
import type { Bars } from '../core/indicators'
import { marketOf } from '../core/market'
import { getJson, HttpError, yahooBase } from './net'

export class SymbolNotFound extends Error {}
export class RateLimited extends Error {}

export interface Quote {
  symbol: string
  price: number
  prev_close: number | null
  currency: string | null
  as_of: string // ISO UTC
  fetched_at: string
  source: string
  realtime: boolean
  name: string | null
  exchange: string | null
  day_open: number | null
  day_high: number | null
  day_low: number | null
  volume: number | null
}

interface ChartResult {
  meta: Record<string, unknown> & { exchangeTimezoneName?: string; regularMarketPrice?: number; regularMarketTime?: number }
  timestamp?: number[]
  indicators: {
    quote: { open: (number | null)[]; high: (number | null)[]; low: (number | null)[]; close: (number | null)[]; volume: (number | null)[] }[]
    adjclose?: { adjclose: (number | null)[] }[]
  }
}

async function chart(symbol: string, params: Record<string, string>): Promise<ChartResult> {
  const qs = new URLSearchParams({ interval: '1d', includeAdjustedClose: 'true', events: 'div,split', ...params })
  let data: { chart?: { result?: ChartResult[] | null; error?: { code?: string; description?: string } | null } }
  try {
    data = await getJson(`${yahooBase()}/v8/finance/chart/${encodeURIComponent(symbol)}?${qs}`)
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) throw new SymbolNotFound(`${symbol}: Yahoo no tiene datos`)
    if (e instanceof HttpError && e.status === 429) throw new RateLimited('Yahoo limitó las consultas; se reintenta más tarde')
    throw new Error(`${symbol}: error de Yahoo (${(e as Error).message})`)
  }
  const err = data.chart?.error
  if (err) {
    if (/not found|no data|delisted/i.test(`${err.code} ${err.description}`)) throw new SymbolNotFound(`${symbol}: Yahoo no tiene datos`)
    throw new Error(`${symbol}: ${err.description ?? err.code}`)
  }
  const r = data.chart?.result?.[0]
  if (!r) throw new SymbolNotFound(`${symbol}: Yahoo no tiene datos`)
  return r
}

/** Fecha de la sesión (AAAA-MM-DD) en la zona horaria de la bolsa. */
function sessionDate(tsSec: number, tz: string): string {
  return new Date(tsSec * 1000).toLocaleDateString('sv-SE', { timeZone: tz })
}

function toBars(r: ChartResult, dropPlaceholders: boolean): Bars {
  const tz = r.meta.exchangeTimezoneName ?? 'UTC'
  const q = r.indicators.quote[0]
  const adj = r.indicators.adjclose?.[0]?.adjclose
  const byDate = new Map<string, number>()
  const out: Bars = { date: [], open: [], high: [], low: [], close: [], volume: [], adj_close: [] }
  ;(r.timestamp ?? []).forEach((ts, i) => {
    const c = q.close[i]
    if (c === null || c === undefined) return
    const o = q.open[i] ?? c
    const h = q.high[i] ?? c
    const l = q.low[i] ?? c
    const v = q.volume[i] ?? 0
    if (dropPlaceholders && v === 0 && o === h && h === l && l === c) return // relleno de Yahoo
    const d = sessionDate(ts, tz)
    const k = byDate.get(d)
    const row = [o, h, l, c, v, adj?.[i] ?? null] as const
    if (k !== undefined) {
      // misma sesión repetida: queda la última
      out.open[k] = row[0]; out.high[k] = row[1]; out.low[k] = row[2]; out.close[k] = row[3]; out.volume[k] = row[4]; out.adj_close![k] = row[5]
      return
    }
    byDate.set(d, out.date.length)
    out.date.push(d); out.open.push(row[0]); out.high.push(row[1]); out.low.push(row[2]); out.close.push(row[3])
    out.volume.push(row[4]); out.adj_close!.push(row[5])
  })
  return out
}

export async function getQuote(symbol: string): Promise<Quote> {
  const r = await chart(symbol, { range: '5d' })
  const bars = toBars(r, false)
  if (!bars.date.length) throw new SymbolNotFound(`${symbol}: sin precios recientes`)
  const m = r.meta
  const n = bars.date.length
  const price = typeof m.regularMarketPrice === 'number' ? m.regularMarketPrice : bars.close[n - 1]
  const asOfSec = typeof m.regularMarketTime === 'number' ? m.regularMarketTime : (r.timestamp ?? [])[n - 1]
  const tz = m.exchangeTimezoneName ?? 'UTC'
  const session = sessionDate(asOfSec, tz)
  // Cierre anterior = sesión previa a la del último precio, tomado de la serie (como en Python).
  let prev: number | null = null
  for (let i = n - 1; i >= 0; i--) {
    if (bars.date[i] < session) {
      prev = bars.close[i]
      break
    }
  }
  const today = bars.date[n - 1] === session ? n - 1 : -1
  return {
    symbol, price, prev_close: prev, currency: (m.currency as string) ?? null,
    as_of: new Date(asOfSec * 1000).toISOString(), fetched_at: new Date().toISOString(), source: 'yahoo', realtime: false,
    name: ((m.longName ?? m.shortName) as string) ?? null, exchange: ((m.fullExchangeName ?? m.exchangeName) as string) ?? null,
    day_open: today >= 0 ? bars.open[today] : null, day_high: today >= 0 ? bars.high[today] : null,
    day_low: today >= 0 ? bars.low[today] : null, volume: today >= 0 ? bars.volume[today] : null,
  }
}

/** Historia diaria desde `start` (AAAA-MM-DD) o de `years` años. */
export async function getHistory(symbol: string, opts: { start?: string; years?: number } = {}): Promise<Bars> {
  const params: Record<string, string> = opts.start
    ? { period1: String(Math.floor(Date.parse(`${opts.start}T00:00:00Z`) / 1000)), period2: String(Math.floor(Date.now() / 1000) + 86400) }
    : { range: `${opts.years ?? 10}y` }
  const r = await chart(symbol, params)
  return toBars(r, marketOf(symbol) !== 'FX')
}
