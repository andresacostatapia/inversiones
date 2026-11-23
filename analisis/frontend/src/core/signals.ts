// Motor de señales: misma lógica que backend/app/signals.py (puntaje combinado, etiqueta, confianza, razones).

import { num, pct, pyRound } from './fmt'
import { computeAll, type Bars, type Frame } from './indicators'
import { clip, nan, notna, rollingMedian, sign, type Series } from './series'
import { STRATEGIES, type Row } from './strategies'

export type Label = 'COMPRAR' | 'MANTENER' | 'VENDER'
export const BUY: Label = 'COMPRAR'
export const HOLD: Label = 'MANTENER'
export const SELL: Label = 'VENDER'
const NEUTRAL_BAND = 10
const HIGH_RV = 1.5
const LOW_RV = 0.7
const HIGH_VOL_RATIO = 1.5
const MIN_WEIGHT = 0.5

export interface SignalsConfig {
  thresholds: { buy: number; sell: number }
  weights: Record<string, number>
  volume_effect: number
}

export const DEFAULT_SIGNALS: SignalsConfig = {
  thresholds: { buy: 30, sell: -30 },
  weights: { trend: 1, rsi: 1, macd: 1, bollinger: 1 },
  volume_effect: 0.2,
}

export const ACTIONS: Record<Label, { holding: string; not_holding: string }> = {
  COMPRAR: { holding: 'Puedes mantener o aumentar la posición.', not_holding: 'Considerar comprar.' },
  MANTENER: { holding: 'Mantener.', not_holding: 'Esperar una mejor entrada.' },
  VENDER: { holding: 'Considerar vender o reducir la posición.', not_holding: 'Evitar comprar por ahora.' },
}

export function activeWeights(cfg: SignalsConfig): [string, number][] {
  return Object.entries(cfg.weights).filter(([k, w]) => k in STRATEGIES && w > 0)
}

export function labelFor(score: number | null | undefined, cfg: SignalsConfig): Label | null {
  if (score === null || score === undefined || Number.isNaN(score)) return null
  if (score >= cfg.thresholds.buy) return BUY
  if (score <= cfg.thresholds.sell) return SELL
  return HOLD
}

/** Puntaje de cada estrategia, el combinado y auxiliares para todos los días (sin mirar el futuro). */
export function scoreFrame(df: Frame, cfg: SignalsConfig, provisionalLast = false): Frame {
  const weights = activeWeights(cfg)
  const n = df.close.length
  const out: Frame = {} as Frame
  const numer = new Array<number>(n).fill(0)
  const den = new Array<number>(n).fill(0)
  for (const [key, w] of weights) {
    const res = STRATEGIES[key].compute(df)
    out[key] = res.score
    for (const [col, s] of Object.entries(res)) if (col !== 'score') out[col] = s
    res.score.forEach((s, i) => {
      if (notna(s)) {
        numer[i] += s * w
        den[i] += w
      }
    })
  }
  const total = weights.reduce((a, [, w]) => a + w, 0) || 1
  const raw = numer.map((x, i) => (den[i] >= MIN_WEIGHT * total ? x / den[i] : NaN))
  out.raw = raw
  out.available_weight = den.map((d) => d / total)

  const rv = df.rel_volume
  const c = df.close
  const dayRet = c.map((x, i) => (i > 0 ? x / c[i - 1] - 1 : NaN))
  const eff = cfg.volume_effect
  const factor = rv.map((v, i) => {
    if (Number.isNaN(v)) return 1
    const same = sign(dayRet[i]) === sign(raw[i]) // NaN nunca es igual: como en numpy
    const nz = raw[i] !== 0 // NaN !== 0 → verdadero, como en numpy
    if (v >= HIGH_RV && same && nz) return 1 + eff
    if (v >= HIGH_RV && !same && nz) return 1 - eff
    if (v < LOW_RV) return 1 - eff / 2
    return 1
  })
  if (provisionalLast && n) factor[n - 1] = 1 // volumen del día incompleto
  out.volume_factor = factor
  out.rel_volume = rv
  out.day_ret = dayRet
  out.composite = raw.map((x, i) => clip(x * factor[i], -100, 100))
  const atrPct = df.atr14_pct
  const med = rollingMedian(atrPct, 252, 60)
  out.vol_ratio = atrPct.map((a, i) => a / med[i])
  return out
}

export interface StrategyView {
  key: string
  label: string
  score: number | null
  weight: number
  reasons: string[]
}

export interface SignalResult {
  symbol: string
  date: string
  label: Label | null
  score: number | null
  confidence: number | null
  provisional: boolean
  summary: string
  reasons: string[]
  strategies: StrategyView[]
  volume_factor: number
  actions: Partial<Record<'holding' | 'not_holding', string>>
  warnings: string[]
  stop_reference: number | null
  thresholds: { buy: number; sell: number }
}

function rowAt(df: Frame, frame: Frame, i: number): Row {
  const r: Row = {}
  for (const [k, s] of Object.entries(df)) r[k] = s[i]
  for (const [k, s] of Object.entries(frame)) r[k] = s[i]
  return r
}

function confidence(row: Row, label: Label, weights: [string, number][], cfg: SignalsConfig, stale: boolean): [number, string[]] {
  const warnings: string[] = []
  const total = weights.reduce((a, [, w]) => a + w, 0) || 1
  const avail = weights.filter(([k]) => notna(row[k]))
  const availW = avail.reduce((a, [, w]) => a + w, 0)
  const c = row.composite
  const { buy, sell } = cfg.thresholds
  let agree = 0
  let strength: number
  if (label === HOLD) {
    for (const [k, w] of avail) if (row[k] > sell && row[k] < buy) agree += w
    strength = 1 - Math.min(1, Math.abs(c) / (c >= 0 ? buy : -sell))
  } else {
    const d = label === BUY ? 1 : -1
    for (const [k, w] of avail) {
      const s = row[k]
      agree += w * (Math.sign(s) === d && Math.abs(s) >= NEUTRAL_BAND ? 1 : Math.abs(s) < NEUTRAL_BAND ? 0.5 : 0)
    }
    const thr = d === 1 ? buy : -sell
    strength = Math.min(1, (Math.abs(c) - thr) / Math.max(1e-9, 100 - thr))
  }
  agree /= availW || 1
  let quality = availW / total
  if (quality < 1) warnings.push('Algunas estrategias no tienen historia suficiente: confianza reducida')
  if (stale) {
    quality *= 0.85
    warnings.push('El precio está desactualizado: confianza reducida')
  }
  const vr = row.vol_ratio
  if (notna(vr) && vr >= HIGH_VOL_RATIO) {
    quality *= 0.8
    warnings.push(`Volatilidad alta (ATR ${num(vr, 1)} veces lo normal): confianza reducida`)
  }
  const conf = 100 * (0.65 * agree + 0.35 * Math.max(0, strength)) * quality
  return [pyRound(conf, 1), warnings]
}

function volumeReason(row: Row, provisional: boolean): string | null {
  const rv = row.rel_volume
  const f = row.volume_factor ?? 1
  if (Number.isNaN(rv)) return null
  if (provisional) return `Volumen del día aún incompleto (${num(rv, 1)}x hasta ahora): no se usa`
  if (f > 1) return `Volumen ${num(rv, 1)}x el promedio confirma el movimiento`
  if (f < 1 && rv >= HIGH_RV) return `Volumen ${num(rv, 1)}x el promedio va contra la señal: la debilita`
  if (f < 1) return `Volumen bajo (${num(rv, 1)}x el promedio): poca convicción`
  return null
}

export function evaluate(symbol: string, df: Frame, dates: string[], cfg: SignalsConfig,
  opts: { provisional?: boolean; stale?: boolean; frame?: Frame } = {}): SignalResult {
  const provisional = !!opts.provisional
  const weights = activeWeights(cfg)
  const frame = opts.frame ?? scoreFrame(df, cfg, provisional)
  const n = dates.length
  const date = n ? dates[n - 1] : ''
  if (!n || Number.isNaN(frame.composite[n - 1])) {
    return {
      symbol, date, label: null, score: null, confidence: null, provisional,
      summary: 'Sin datos suficientes para generar una señal', reasons: [], strategies: [], volume_factor: 1,
      actions: {}, warnings: ['Se necesitan al menos 20 días de historia (y 200 para todas las estrategias)'],
      stop_reference: null, thresholds: { ...cfg.thresholds },
    }
  }
  const row = rowAt(df, frame, n - 1)
  const score = row.composite
  const label = labelFor(score, cfg) as Label
  const [conf, warnings] = confidence(row, label, weights, cfg, !!opts.stale)

  const views: StrategyView[] = weights.map(([key, w]) => ({
    key, label: STRATEGIES[key].label, score: Number.isNaN(row[key]) ? null : pyRound(row[key], 1), weight: w,
    reasons: STRATEGIES[key].explain(row),
  }))

  const scored = views.filter((v) => v.score !== null)
  let lead: StrategyView[]
  if (label === HOLD) {
    lead = [...scored].sort((a, b) => Math.abs(b.score!) - Math.abs(a.score!)).slice(0, 2)
  } else {
    const d = label === BUY ? 1 : -1
    lead = scored.filter((v) => Math.sign(v.score!) === d)
      .sort((a, b) => Math.abs(b.score!) * b.weight - Math.abs(a.score!) * a.weight).slice(0, 2)
  }
  let summary: string
  if (label === HOLD && lead.length === 2 && Math.sign(lead[0].score!) !== Math.sign(lead[1].score!)) {
    summary = `Señales mixtas: ${lead[0].reasons[0]} vs. ${lead[1].reasons[0]}`
  } else {
    summary = lead.length ? lead.map((v) => v.reasons[0]).join(' + ') : 'Señales mixtas'
  }

  const reasons = views.flatMap((v) => v.reasons)
  const vr = volumeReason(row, provisional)
  if (vr) reasons.push(vr)
  if (provisional) warnings.unshift('Señal provisional: el mercado está abierto y la vela de hoy puede cambiar hasta el cierre')

  let stop: number | null = null
  if (notna(row.atr14)) {
    stop = pyRound(row.close - 2 * row.atr14, 4)
    reasons.push(`Stop de referencia: ${num(stop)} (cierre − 2×ATR, ${pct(-2 * row.atr14_pct)})`)
  }

  return {
    symbol, date, label, score: pyRound(score, 1), confidence: conf, provisional, summary, reasons, strategies: views,
    volume_factor: pyRound(row.volume_factor, 3), actions: ACTIONS[label], warnings, stop_reference: stop,
    thresholds: { ...cfg.thresholds },
  }
}

export function signalForBars(symbol: string, bars: Bars, cfg: SignalsConfig, opts: { provisional?: boolean; stale?: boolean } = {}) {
  return evaluate(symbol, computeAll(bars), bars.date, cfg, opts)
}

export interface ScorePoint {
  date: string
  composite: number | null
  label: Label | null
  [k: string]: number | string | null
}

/** Puntaje diario de cada estrategia y del combinado (para el gráfico de evolución). */
export function scoreHistory(bars: Bars, cfg: SignalsConfig): ScorePoint[] {
  const df = computeAll(bars)
  const frame = scoreFrame(df, cfg)
  const keys = activeWeights(cfg).map(([k]) => k)
  return bars.date.map((d, i) => {
    const p: ScorePoint = { date: d, composite: null, label: null }
    for (const k of [...keys, 'composite']) {
      const v = frame[k][i]
      p[k] = Number.isNaN(v) ? null : pyRound(v, 1)
    }
    p.label = labelFor(frame.composite[i], cfg)
    return p
  })
}

export type { Series }
export { nan }
