// El motor TypeScript (app Android) debe dar lo mismo que el de Python.
// Los casos se generan con: cd analisis/backend && python scripts/export_fixtures.py
import { describe, expect, it } from 'vitest'
import golden from './__fixtures__/golden.json'
import { runBacktest, type BacktestConfig } from './backtest'
import { pyRound } from './fmt'
import { basicMetrics, computeAll, type Bars, type Frame } from './indicators'
import { freshness, isOpen, marketOf, type Market } from './market'
import { buildPositions, investability, suggestContribution, valuePortfolio, type Candidate, type Tx } from './portfolio'
import { DEFAULT_SIGNALS, evaluate, scoreFrame, type SignalsConfig } from './signals'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const G = golden as any

/** Compara en profundidad con tolerancia numérica; devuelve la primera diferencia o null. */
function diff(actual: unknown, expected: unknown, tol: number, path = '$'): string | null {
  const isNullish = (v: unknown) => v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v))
  if (typeof expected === 'number' || typeof actual === 'number') {
    if (isNullish(expected) && isNullish(actual)) return null
    if (typeof expected !== 'number' || typeof actual !== 'number') return `${path}: ${String(actual)} ≠ ${String(expected)}`
    const d = Math.abs(actual - expected)
    return d <= tol * Math.max(1, Math.abs(expected)) ? null : `${path}: ${actual} ≠ ${expected}`
  }
  if (isNullish(expected) || isNullish(actual)) return isNullish(expected) && isNullish(actual) ? null : `${path}: ${JSON.stringify(actual)} ≠ ${JSON.stringify(expected)}`
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) return `${path}: no es lista`
    if (actual.length !== expected.length) return `${path}: largo ${actual.length} ≠ ${expected.length}`
    for (let i = 0; i < expected.length; i++) {
      const d = diff(actual[i], expected[i], tol, `${path}[${i}]`)
      if (d) return d
    }
    return null
  }
  if (typeof expected === 'object') {
    for (const k of Object.keys(expected as object)) {
      const d = diff((actual as Record<string, unknown>)?.[k], (expected as Record<string, unknown>)[k], tol, `${path}.${k}`)
      if (d) return d
    }
    return null
  }
  return actual === expected ? null : `${path}: ${JSON.stringify(actual)} ≠ ${JSON.stringify(expected)}`
}

const toBars = (b: Record<string, (number | null)[]> & { date: string[] }): Bars => ({
  date: b.date,
  open: b.open as number[], high: b.high as number[], low: b.low as number[], close: b.close as number[],
  volume: b.volume as number[], adj_close: b.adj_close,
})

const sliceFrame = (f: Frame, n: number): Frame => Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.slice(0, n)])) as Frame

describe.each(['up', 'down', 'flat'])('serie %s', (name) => {
  const c = G[name]
  const bars = toBars(c.bars)
  const df = computeAll(bars)
  const frame = scoreFrame(df, DEFAULT_SIGNALS)

  it('indicadores idénticos a pandas', () => {
    for (const [col, expected] of Object.entries(c.indicators)) {
      expect(diff(df[col], expected, 1e-9, col)).toBeNull()
    }
  })

  it('puntajes de estrategias, combinado y auxiliares', () => {
    for (const [col, expected] of Object.entries(c.frame)) {
      expect(diff(frame[col], expected, 1e-9, col)).toBeNull()
    }
  })

  it('métricas básicas', () => {
    expect(diff(basicMetrics(bars.close), c.metrics, 1e-9)).toBeNull()
  })

  it('señal: etiqueta, puntaje, confianza, resumen y razones', () => {
    for (const e of c.evals) {
      const n = e.cut + 1
      const r = evaluate(name, sliceFrame(df, n), bars.date.slice(0, n), DEFAULT_SIGNALS,
        { provisional: e.provisional, stale: e.stale })
      const where = `${name} día ${e.cut} prov=${e.provisional} stale=${e.stale}`
      expect(diff(r, e.result, 1e-9, where)).toBeNull()
    }
  })
})

it('configuración alternativa (pesos, umbrales, volumen)', () => {
  const c = G.alt_config
  const cfg = c.config as SignalsConfig
  const bars = toBars(G.flat.bars)
  const df = computeAll(bars)
  const frame = scoreFrame(df, cfg)
  for (const [col, expected] of Object.entries(c.frame)) expect(diff(frame[col], expected, 1e-9, col)).toBeNull()
  expect(diff(evaluate('flat', df, bars.date, cfg), c.eval, 1e-9)).toBeNull()
})

describe('backtest', () => {
  const bars = toBars(G.backtest.bars)
  it('2 años, comisión y deslizamiento', () => {
    const bt: BacktestConfig = { commission_pct: 0.5, slippage_pct: 0.1, default_years: 10 }
    expect(diff(runBacktest('X', bars, DEFAULT_SIGNALS, bt, { years: 2 }), G.backtest.result, 1e-9)).toBeNull()
  })
  it('toda la historia, subconjunto de estrategias', () => {
    const bt: BacktestConfig = { commission_pct: 0, slippage_pct: 0, default_years: 10 }
    expect(diff(runBacktest('X', bars, DEFAULT_SIGNALS, bt, { years: null, strategies: ['trend', 'rsi'] }),
      G.backtest.result_all, 1e-9)).toBeNull()
  })
})

describe('cartera', () => {
  const p = G.portfolio
  it('posiciones y valorización', () => {
    const pos = buildPositions(p.txs as Tx[])
    const v = valuePortfolio(pos, p.prices, 1016, { CL: 0.3, INTL: 0.7 }, -20, -25)
    expect(diff(v, p.valuation, 1e-9)).toBeNull()
  })
  it('reparto del aporte', () => {
    for (const c of p.contributions) {
      const r = suggestContribution(c.amount, p.bucket_values, p.candidates as Candidate[], { CL: 0.3, INTL: 0.7 }, c.fractional)
      expect(diff(r, c.result, 1e-9, `aporte ${c.amount}`)).toBeNull()
    }
  })
})

describe('mercado', () => {
  it('clasificación de símbolos', () => {
    for (const [sym, m] of Object.entries(G.market.markets)) expect([sym, marketOf(sym)]).toEqual([sym, m])
  })
  it('qué permite comprar el perfil', () => {
    for (const c of G.market.investability) {
      const [sym, kind, cur, name] = c.args
      const [d, l, sh] = c.flags ?? [false, false, false]
      expect([sym, investability(sym, kind, cur, name, d, l, sh)]).toEqual([sym, c.result])
    }
  })
  it('horarios con feriados y cierres anticipados', () => {
    for (const h of G.market.hours) {
      const t = new Date(h.t)
      const got = Object.fromEntries((['CL', 'US', 'FX', 'GLB', 'CRY', 'OTHER'] as Market[]).map((m) => [m, isOpen(m, t)]))
      expect({ t: h.t, ...got }).toEqual(h)
    }
  })
  it('etiquetas de frescura', () => {
    const now = new Date(G.market.now)
    for (const f of G.market.freshness) {
      const r = freshness(new Date(f.as_of), { realtime: f.realtime, marketOpen: f.market_open, now, daily: f.daily,
        hoursKnown: f.hours_known })
      expect({ ...r, age_minutes: pyRound(r.age_minutes ?? NaN, 1) }).toEqual(f.result)
    }
  })
})
