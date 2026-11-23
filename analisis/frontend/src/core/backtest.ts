// Backtest: misma lógica que backend/app/backtest.py (señal al cierre, ejecución en la apertura siguiente,
// solo largo, comisión por lado, precios ajustados por dividendos, comparación con buy & hold).

import { pct, pyRound } from './fmt'
import { computeAll, TRADING_DAYS, type Bars } from './indicators'
import { std } from './series'
import { activeWeights, scoreFrame, type SignalsConfig } from './signals'
import { STRATEGIES } from './strategies'

export const WARNING = 'Resultados pasados no garantizan resultados futuros. Un backtest favorable puede deberse a '
  + 'sobreajuste: no ajustes pesos y umbrales hasta que el pasado se vea bien.'

export interface BacktestConfig {
  commission_pct: number
  slippage_pct: number
  default_years: number
}

export const DEFAULT_BACKTEST: BacktestConfig = { commission_pct: 0.5, slippage_pct: 0, default_years: 10 }

export interface Trade {
  entry_date: string
  entry_price: number
  exit_date: string | null
  exit_price: number | null
  return_pct: number
  open: boolean
}

interface Sim {
  dates: string[]
  equity: number[]
  inMarket: number[]
  trades: Trade[]
}

export function adjusted(b: Bars): Bars {
  const adj = b.adj_close
  if (!adj || adj.every((x) => x === null || Number.isNaN(x))) return { ...b, adj_close: undefined }
  const f = b.close.map((c, i) => {
    const a = adj[i]
    const r = a === null || a === undefined ? NaN : a / c
    return Number.isNaN(r) ? 1 : r
  })
  return {
    date: b.date, volume: b.volume,
    open: b.open.map((x, i) => x * f[i]), high: b.high.map((x, i) => x * f[i]),
    low: b.low.map((x, i) => x * f[i]), close: b.close.map((x, i) => x * f[i]),
  }
}

export function simulate(dates: string[], o: number[], c: number[], s: number[], buy: number, sell: number,
  cost: number, start: number): Sim {
  const n = c.length
  const eq = new Array<number>(n - start).fill(1)
  const pos = new Array<number>(n - start).fill(0)
  const trades: Trade[] = []
  let equity = 1
  let p = 0
  let entryPx = 0
  let entryEq = 1
  let entryDate = ''
  for (let i = start + 1; i < n; i++) {
    const sig = s[i - 1]
    let target = p
    if (!Number.isNaN(sig)) {
      if (p === 0 && sig >= buy) target = 1
      else if (p === 1 && sig <= sell) target = 0
    }
    if (p === 0 && target === 1) {
      entryEq = equity
      entryPx = o[i]
      entryDate = dates[i]
      equity *= 1 - cost
      equity *= c[i] / o[i]
    } else if (p === 1 && target === 1) {
      equity *= c[i] / c[i - 1]
    } else if (p === 1 && target === 0) {
      equity *= o[i] / c[i - 1]
      equity *= 1 - cost
      trades.push({ entry_date: entryDate, entry_price: pyRound(entryPx, 4), exit_date: dates[i],
        exit_price: pyRound(o[i], 4), return_pct: pyRound((equity / entryEq - 1) * 100, 2), open: false })
    }
    p = target
    eq[i - start] = equity
    pos[i - start] = p
  }
  if (p === 1) {
    trades.push({ entry_date: entryDate, entry_price: pyRound(entryPx, 4), exit_date: null, exit_price: null,
      return_pct: pyRound((equity / entryEq - 1) * 100, 2), open: true })
  }
  return { dates: dates.slice(start), equity: eq, inMarket: pos, trades }
}

export function buyAndHold(dates: string[], o: number[], c: number[], cost: number, start: number): Sim {
  return simulate(dates, o, c, new Array<number>(c.length).fill(Infinity), 0, -Infinity, cost, start)
}

const dayNum = (d: string) => Date.parse(`${d}T00:00:00Z`) / 864e5

export interface BtMetrics {
  total_return_pct: number
  cagr_pct: number
  max_drawdown_pct: number
  volatility_pct: number
  sharpe: number | null
  trades: number
  win_rate_pct: number | null
  avg_trade_pct: number | null
  exposure_pct: number
  open_position: boolean
}

export function metrics(sim: Sim): BtMetrics | Record<string, never> {
  const eq = sim.equity
  if (eq.length < 2) return {}
  const years = Math.max((dayNum(sim.dates[sim.dates.length - 1]) - dayNum(sim.dates[0])) / 365.25, 1e-9)
  const total = eq[eq.length - 1] / eq[0] - 1
  let peak = -Infinity
  let dd = 0
  for (const v of eq) {
    peak = Math.max(peak, v)
    dd = Math.min(dd, v / peak - 1)
  }
  const daily = eq.slice(1).map((v, i) => v / eq[i] - 1)
  const vol = daily.length > 1 ? std(daily) * Math.sqrt(TRADING_DAYS) : 0
  const mean = daily.reduce((a, b) => a + b, 0) / daily.length
  const closed = sim.trades.filter((t) => !t.open)
  const wins = closed.filter((t) => t.return_pct > 0)
  const exposure = sim.inMarket.slice(1).reduce((a, b) => a + b, 0) / (sim.inMarket.length - 1)
  return {
    total_return_pct: pyRound(total * 100, 2),
    cagr_pct: total > -1 ? pyRound(((1 + total) ** (1 / years) - 1) * 100, 2) : -100,
    max_drawdown_pct: pyRound(dd * 100, 2),
    volatility_pct: pyRound(vol * 100, 2),
    sharpe: vol ? pyRound((mean * TRADING_DAYS) / vol, 2) : null,
    trades: closed.length,
    win_rate_pct: closed.length ? pyRound((wins.length / closed.length) * 100, 1) : null,
    // Promedio en centésimas enteras: exacto e idéntico en Python y TypeScript.
    avg_trade_pct: closed.length
      ? pyRound(closed.reduce((a, t) => a + Math.round(t.return_pct * 100), 0) / closed.length / 100, 2) : null,
    exposure_pct: pyRound(exposure * 100, 1),
    open_position: !!(sim.trades.length && sim.trades[sim.trades.length - 1].open),
  }
}

/** Curva semanal: último valor de cada semana, rotulada con el viernes (como pandas resample('W-FRI')). */
export function weekly(sim: Sim): { date: string; value: number }[] {
  const out: { date: string; value: number }[] = []
  const iso = (n: number) => new Date(n * 864e5).toISOString().slice(0, 10)
  for (let i = 0; i < sim.dates.length; i++) {
    const d = dayNum(sim.dates[i])
    const wd = (new Date(d * 864e5).getUTCDay() + 6) % 7 // lunes = 0 … viernes = 4
    const friday = iso(d + ((4 - wd + 7) % 7))
    const v = pyRound(sim.equity[i], 5)
    if (out.length && out[out.length - 1].date === friday) out[out.length - 1].value = v
    else out.push({ date: friday, value: v })
  }
  return out
}

function verdict(m: BtMetrics, bh: BtMetrics): string {
  const ret = m.total_return_pct > bh.total_return_pct ? 'superó' : 'no superó'
  const diff = m.max_drawdown_pct - bh.max_drawdown_pct
  const dd = Math.abs(diff) < 0.05 ? 'con la misma caída máxima' : diff > 0 ? 'con menor caída máxima' : 'con mayor caída máxima'
  return `${ret} a buy & hold en retorno (${pct(m.total_return_pct)} vs ${pct(bh.total_return_pct)}), `
    + `${dd} (${pct(m.max_drawdown_pct)} vs ${pct(bh.max_drawdown_pct)})`
}

export function runBacktest(symbol: string, bars: Bars, sig: SignalsConfig, bt: BacktestConfig,
  opts: { years?: number | null; strategies?: string[] | null; includeCurves?: boolean; includeTrades?: boolean } = {}) {
  const keep = bars.close.map((c, i) => !Number.isNaN(c) && !Number.isNaN(bars.open[i]))
  const pick = <T,>(a: T[]) => a.filter((_, i) => keep[i])
  const clean: Bars = {
    date: pick(bars.date), open: pick(bars.open), high: pick(bars.high), low: pick(bars.low), close: pick(bars.close),
    volume: pick(bars.volume), adj_close: bars.adj_close ? pick(bars.adj_close) : undefined,
  }
  const data = adjusted(clean)
  const df = computeAll(data)
  const frame = scoreFrame(df, sig)
  const weights = activeWeights(sig)
  const keys = (opts.strategies ?? weights.map(([k]) => k)).filter((k) => k in STRATEGIES)
  const n = data.date.length
  let start = -1
  for (let i = 0; i < n; i++) {
    if ([...keys, 'composite'].every((k) => !Number.isNaN(frame[k][i])) && !Number.isNaN(df.sma200[i])) {
      start = i
      break
    }
  }
  if (start < 0) throw new Error(`${symbol}: historia insuficiente para el backtest (se necesitan más de 200 días)`)
  const years = opts.years
  if (years) {
    const cutoff = dayNum(data.date[n - 1]) - Math.trunc(years * 365.25)
    const pos = data.date.findIndex((d) => dayNum(d) >= cutoff)
    start = Math.max(start, pos < 0 ? n : pos)
  }
  if (start >= n - 2) throw new Error(`${symbol}: periodo demasiado corto para el backtest`)

  const cost = (bt.commission_pct + bt.slippage_pct) / 100
  const { buy, sell } = sig.thresholds
  const bhSim = buyAndHold(data.date, data.open, data.close, cost, start)
  const bh = metrics(bhSim) as BtMetrics
  const curves = opts.includeCurves ?? true
  const trades = opts.includeTrades ?? true

  const results = [...keys, 'composite'].map((key) => {
    const sim = simulate(data.date, data.open, data.close, frame[key], buy, sell, cost, start)
    const m = metrics(sim) as BtMetrics
    return {
      key,
      label: key === 'composite' ? 'Combinada' : STRATEGIES[key].label,
      metrics: m,
      vs_buy_hold: {
        return_diff_pct: pyRound(m.total_return_pct - bh.total_return_pct, 2),
        drawdown_diff_pct: pyRound(m.max_drawdown_pct - bh.max_drawdown_pct, 2),
        verdict: verdict(m, bh),
      },
      ...(curves ? { equity: weekly(sim) } : {}),
      ...(trades ? { trades: sim.trades } : {}),
    }
  })
  const hasAdj = !!bars.adj_close && bars.adj_close.some((x) => x !== null && !Number.isNaN(x as number))
  return {
    symbol,
    start: data.date[start],
    end: data.date[n - 1],
    years: pyRound((dayNum(data.date[n - 1]) - dayNum(data.date[start])) / 365.25, 2),
    adjusted_prices: hasAdj,
    assumptions: {
      execution: 'señal al cierre, ejecución en la apertura del día siguiente',
      position: 'solo largo, 100 % dentro o fuera',
      commission_pct: bt.commission_pct,
      slippage_pct: bt.slippage_pct,
      thresholds: { buy, sell },
      weights: Object.fromEntries(weights),
    },
    buy_hold: { metrics: bh, ...(curves ? { equity: weekly(bhSim) } : {}) },
    strategies: results,
    warning: WARNING,
  }
}
