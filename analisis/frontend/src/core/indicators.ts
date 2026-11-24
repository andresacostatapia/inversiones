// Indicadores técnicos: misma lógica y convenciones que backend/app/indicators.py
// (EMA y RMA sembradas con SMA, RSI/ATR de Wilder, Bollinger con desviación poblacional,
// volumen relativo contra los n días ANTERIORES).

import { clip, map2, nan, notna, rollingMean, rollingStdPop, shift, std, type Series } from './series'

export const TRADING_DAYS = 252

export interface Bars {
  date: string[] // AAAA-MM-DD
  open: number[]
  high: number[]
  low: number[]
  close: number[]
  volume: number[]
  adj_close?: (number | null)[]
}

export type Frame = Record<string, Series> & { date?: never }

export const sma = (close: Series, n: number) => rollingMean(close, n)

function seededEwm(values: Series, n: number, alpha: number): Series {
  const out = nan(values.length)
  const idx: number[] = []
  values.forEach((v, i) => notna(v) && idx.push(i))
  if (idx.length < n) return out
  let prev = 0
  for (let k = 0; k < n; k++) prev += values[idx[k]]
  prev /= n
  out[idx[n - 1]] = prev
  for (let k = n; k < idx.length; k++) {
    prev = alpha * values[idx[k]] + (1 - alpha) * prev
    out[idx[k]] = prev
  }
  return out
}

export const ema = (close: Series, n: number) => seededEwm(close, n, 2 / (n + 1))
export const rma = (values: Series, n: number) => seededEwm(values, n, 1 / n)

export function rsi(close: Series, n = 14): Series {
  const delta = map2(close, shift(close, 1), (a, b) => a - b)
  const gain = delta.map((d) => (Number.isNaN(d) ? NaN : Math.max(d, 0)))
  const loss = delta.map((d) => (Number.isNaN(d) ? NaN : Math.max(-d, 0)))
  const ag = rma(gain, n)
  const al = rma(loss, n)
  return ag.map((g, i) => {
    const l = al[i]
    if (Number.isNaN(g)) return NaN
    if (g === 0 && l === 0) return 50
    if (l === 0) return 100
    return 100 - 100 / (1 + g / l)
  })
}

export function macd(close: Series, fast = 12, slow = 26, signal = 9) {
  const line = map2(ema(close, fast), ema(close, slow), (a, b) => a - b)
  const sig = ema(line, signal)
  return { macd: line, macd_signal: sig, macd_hist: map2(line, sig, (a, b) => a - b) }
}

export function bollinger(close: Series, n = 20, k = 2) {
  const mid = sma(close, n)
  const sd = rollingStdPop(close, n)
  const upper = map2(mid, sd, (m, s) => m + k * s)
  const lower = map2(mid, sd, (m, s) => m - k * s)
  const width = map2(upper, lower, (u, l) => u - l)
  const pctb = close.map((c, i) => (c - lower[i]) / width[i]) // ancho 0 → ±Infinity o NaN, como pandas
  const bw = map2(width, mid, (w, m) => w / m)
  return { bb_mid: mid, bb_upper: upper, bb_lower: lower, bb_pctb: pctb, bb_width: bw }
}

export function trueRange(high: Series, low: Series, close: Series): Series {
  return high.map((h, i) => {
    const l = low[i]
    if (i === 0) return h - l
    const p = close[i - 1]
    const c = [h - l, Math.abs(h - p), Math.abs(l - p)].filter((x) => !Number.isNaN(x))
    return c.length ? Math.max(...c) : NaN
  })
}

export const atr = (high: Series, low: Series, close: Series, n = 14) => rma(trueRange(high, low, close), n)

export function relativeVolume(volume: Series, n = 20): Series {
  const avg = rollingMean(shift(volume, 1), n)
  return volume.map((v, i) => (avg[i] > 0 ? v / avg[i] : NaN))
}

/** Todas las columnas de indicadores (mismos nombres que compute_all en Python). */
export function computeAll(b: Bars): Frame {
  const c = b.close
  const f: Frame = { open: b.open, high: b.high, low: b.low, close: c, volume: b.volume } as Frame
  for (const n of [20, 50, 200]) {
    f[`sma${n}`] = sma(c, n)
    f[`ema${n}`] = ema(c, n)
  }
  f.rsi14 = rsi(c, 14)
  Object.assign(f, macd(c), bollinger(c))
  f.atr14 = atr(b.high, b.low, c, 14)
  f.atr14_pct = map2(f.atr14, c, (a, x) => (a / x) * 100)
  f.rel_volume = relativeVolume(b.volume, 20)
  return f
}

export interface Metrics {
  return_1m_pct: number | null
  return_3m_pct: number | null
  return_6m_pct: number | null
  return_1y_pct: number | null
  volatility_1y_pct: number | null
  high_52w: number | null
  low_52w: number | null
  from_high_52w_pct: number | null
  dist_sma200_pct: number | null
}

export function basicMetrics(close: Series): Metrics | Record<string, never> {
  const c = close.filter(notna)
  if (!c.length) return {}
  const last = c[c.length - 1]
  const ret = (d: number) => (c.length <= d ? null : (last / c[c.length - 1 - d] - 1) * 100)
  const year = c.slice(-TRADING_DAYS)
  const daily = c.slice(1).map((x, i) => x / c[i] - 1)
  const hi = Math.max(...year)
  const lo = Math.min(...year)
  const s200 = sma(c, 200)[c.length - 1]
  return {
    return_1m_pct: ret(21),
    return_3m_pct: ret(63),
    return_6m_pct: ret(126),
    return_1y_pct: ret(TRADING_DAYS),
    volatility_1y_pct: daily.length > 20 ? std(daily.slice(-TRADING_DAYS)) * Math.sqrt(TRADING_DAYS) * 100 : null,
    high_52w: hi,
    low_52w: lo,
    from_high_52w_pct: (last / hi - 1) * 100,
    dist_sma200_pct: Number.isNaN(s200) ? null : (last / s200 - 1) * 100,
  }
}

export { clip }
