// Estrategias: misma lógica y mismas frases que backend/app/strategies/*.py.
// compute() devuelve el puntaje (-100..100, NaN sin datos) de TODOS los días + columnas auxiliares.

import { days, num, pct } from './fmt'
import type { Frame } from './indicators'
import { barsSince, clip, crossed, interp, nan, notna, type Series } from './series'

export type Row = Record<string, number>

export interface Strategy {
  key: string
  label: string
  description: string
  compute(df: Frame): Record<string, Series> & { score: Series }
  explain(row: Row): string[]
}

const isnan = Number.isNaN
const valid2 = (a: number, b: number) => !isnan(a) && !isnan(b)

const trend: Strategy = {
  key: 'trend',
  label: 'Tendencia',
  description: 'Precio vs SMA200, SMA50 vs SMA200 (cruce dorado/de la muerte) y pendiente de la EMA20.',
  compute(df) {
    const { close: c, sma50: s50, sma200: s200, ema20: e20 } = df
    const n = c.length
    const dist = c.map((x, i) => (x / s200[i] - 1) * 100)
    const golden = c.map((_, i) => (valid2(s50[i], s200[i]) ? (s50[i] > s200[i] ? 1 : 0) : NaN))
    const slope = e20.map((x, i) => (i >= 5 ? (x / e20[i - 5] - 1) * 100 : NaN))
    const score = nan(n)
    for (let i = 0; i < n; i++) {
      const pDist = clip(dist[i] / 5, -1, 1) * 35
      const pCross = isnan(golden[i]) ? NaN : golden[i] === 1 ? 35 : -35
      const pSlope = clip(slope[i] / 2, -1, 1) * 30
      score[i] = pDist + pCross + pSlope
    }
    return { score, trend_dist200: dist, trend_golden: golden, trend_cross_age: barsSince(crossed(golden)), trend_slope: slope }
  },
  explain(r) {
    if (isnan(r.trend_dist200) || isnan(r.trend_golden)) return ['Tendencia: faltan datos (se necesitan 200 días de historia)']
    const d = r.trend_dist200
    const out = [`Precio ${pct(Math.abs(d), 1, false)} ${d >= 0 ? 'sobre' : 'bajo'} la SMA200 (tendencia de largo plazo ${d >= 0 ? 'alcista' : 'bajista'})`]
    const age = r.trend_cross_age
    const g = r.trend_golden === 1
    if (notna(age) && age <= 20) {
      out.push(`${g ? 'Cruce dorado' : 'Cruce de la muerte'} (SMA50 cruzó ${g ? 'sobre' : 'bajo'} la SMA200) ${days(age)}`)
    } else {
      out.push(`SMA50 ${g ? 'sobre' : 'bajo'} la SMA200`)
    }
    const sl = r.trend_slope
    if (notna(sl)) {
      const word = sl > 0.2 ? 'subiendo' : sl < -0.2 ? 'bajando' : 'plana'
      out.push(`EMA20 ${word} (${pct(sl)} en 5 días)`)
    }
    return out
  },
}

const XP = [20, 30, 40, 60, 70, 80]
const FP = [100, 60, 20, -20, -60, -100]

const rsi: Strategy = {
  key: 'rsi',
  label: 'RSI',
  description: 'RSI(14) bajo 30 = sobreventa (compra), sobre 70 = sobrecompra (venta).',
  compute(df) {
    const r = df.rsi14
    const score = r.map((x) => (isnan(x) ? NaN : interp(x, XP, FP)))
    const exitOs = r.map((x, i) => (i > 0 && x > 30 && r[i - 1] <= 30 ? 1 : 0))
    const exitOb = r.map((x, i) => (i > 0 && x < 70 && r[i - 1] >= 70 ? 1 : 0))
    return { score, rsi_exit_oversold_age: barsSince(exitOs), rsi_exit_overbought_age: barsSince(exitOb) }
  },
  explain(row) {
    const r = row.rsi14
    if (isnan(r)) return ['RSI: faltan datos']
    const zone = r < 30 ? 'sobreventa' : r < 40 ? 'cerca de sobreventa' : r <= 60 ? 'zona neutral' : r <= 70 ? 'cerca de sobrecompra' : 'sobrecompra'
    const out = [`RSI ${num(r, 0)}: ${zone}`]
    let a = row.rsi_exit_oversold_age
    if (notna(a) && a <= 5 && r > 30 && r < 50) out.push(`RSI salió de sobreventa ${days(a)}`)
    a = row.rsi_exit_overbought_age
    if (notna(a) && a <= 5 && r > 50 && r < 70) out.push(`RSI salió de sobrecompra ${days(a)}`)
    return out
  },
}

const FRESH_DAYS = 3

const macd: Strategy = {
  key: 'macd',
  label: 'MACD',
  description: 'Posición del MACD frente a su señal, cruces recientes y signo del MACD.',
  compute(df) {
    const { macd: m, macd_signal: s } = df
    const above = m.map((x, i) => (valid2(x, s[i]) ? (x > s[i] ? 1 : 0) : NaN))
    const age = barsSince(crossed(above))
    const score = m.map((x, i) => {
      if (!valid2(x, s[i])) return NaN
      const sg = above[i] === 1 ? 1 : -1
      return 40 * sg + (age[i] <= FRESH_DAYS ? 40 * sg : 0) + (x > 0 ? 20 : -20)
    })
    return { score, macd_above: above, macd_cross_age: age }
  },
  explain(r) {
    if (isnan(r.macd_above)) return ['MACD: faltan datos']
    const above = r.macd_above === 1
    const age = r.macd_cross_age
    const out = [notna(age) && age <= 10
      ? `Cruce MACD ${above ? 'alcista' : 'bajista'} ${days(age)}`
      : `MACD ${above ? 'sobre' : 'bajo'} su línea de señal`]
    out.push(`MACD ${r.macd > 0 ? 'positivo' : 'negativo'} (momentum de fondo ${r.macd > 0 ? 'alcista' : 'bajista'})`)
    return out
  },
}

const bollinger: Strategy = {
  key: 'bollinger',
  label: 'Bollinger',
  description: 'Precio cerca de la banda inferior (compra) o superior (venta), filtrado por la tendencia.',
  compute(df) {
    const { bb_pctb: b, close: c, sma200: s200 } = df
    const score = nan(b.length)
    const against = nan(b.length)
    for (let i = 0; i < b.length; i++) {
      if (isnan(b[i])) continue
      const raw = Math.min(100, Math.max(-100, (0.5 - b[i]) * 160))
      const has200 = notna(s200[i])
      const above = c[i] > s200[i]
      const ag = has200 && ((raw > 0 && !above) || (raw < 0 && above))
      against[i] = ag ? 1 : 0
      score[i] = ag ? raw / 2 : raw
    }
    return { score, bb_against_trend: against }
  },
  explain(r) {
    const b = r.bb_pctb
    if (isnan(b)) return ['Bollinger: faltan datos']
    const pos = b <= 0 ? 'bajo la banda inferior' : b < 0.2 ? 'cerca de la banda inferior' : b <= 0.8 ? 'dentro de las bandas'
      : b < 1 ? 'cerca de la banda superior' : 'sobre la banda superior'
    const out = [`Precio ${pos} de Bollinger (%B ${num(b, 2)})`]
    if (r.bb_against_trend === 1) out.push('…pero va contra la tendencia de largo plazo: señal reducida a la mitad')
    return out
  },
}

export const STRATEGIES: Record<string, Strategy> = { trend, rsi, macd, bollinger }
