import { useState } from 'react'
import { api, type Period } from '../api'
import { money } from '../format'
import { usePolling, type ChartColors } from '../hooks'
import type { QuoteView } from '../types'
import { BacktestPanel } from './BacktestPanel'
import { Change, FreshnessBadge } from './Badges'
import { Metrics } from './Metrics'
import { PriceChart } from './PriceChart'
import { ScoreChart } from './ScoreChart'
import { SignalCard } from './SignalCard'

const PERIODS: [Period, string][] = [['3m', '3M'], ['6m', '6M'], ['1y', '1A'], ['2y', '2A'], ['5y', '5A'], ['10y', '10A']]
const MARKET = { CL: 'Bolsa de Santiago', US: 'EE.UU.', FX: 'Divisas' } as const

function PeriodPicker({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div className="seg" role="group" aria-label="Periodo">
      {PERIODS.map(([p, label]) => (
        <button key={p} type="button" aria-pressed={value === p} onClick={() => onChange(p)}>{label}</button>
      ))}
    </div>
  )
}

export function Detail({ symbol, quote, colors, refreshMs }: {
  symbol: string
  quote: QuoteView | undefined
  colors: ChartColors
  refreshMs: number
}) {
  const [period, setPeriod] = useState<Period>(() => {
    try {
      return (localStorage.getItem('period') as Period) || '1y'
    } catch {
      return '1y'
    }
  })
  const choose = (p: Period) => {
    setPeriod(p)
    try {
      localStorage.setItem('period', p)
    } catch {
      /* sin almacenamiento */
    }
  }

  const signal = usePolling(() => api.signal(symbol), refreshMs, [symbol])
  const ind = usePolling(() => api.indicators(symbol), refreshMs, [symbol])
  const hist = usePolling(() => api.history(symbol, period), refreshMs, [symbol, period])
  const scores = usePolling(() => api.scoreHistory(symbol, period), refreshMs * 5, [symbol, period])
  const q = ind.data?.quote ?? quote
  const currency = q?.currency ?? null

  return (
    <div className="col">
      <section className="card" aria-labelledby="hero-title">
        <div className="hero-title">
          <h2 id="hero-title">{symbol}</h2>
          <span className="muted">{q?.name ?? ''}</span>
          {q && <span className="chip">{MARKET[q.market]}</span>}
        </div>
        <div className="hero">
          <span className="price num">{money(q?.price, currency)}</span>
          <span className="chg"><Change v={q?.change_pct} /></span>
          <FreshnessBadge f={q?.freshness} />
        </div>
        <p className="muted small" style={{ margin: '4px 0 0' }}>
          Fuente: {q?.source ?? '—'}
          {q?.status === 'error' && <span className="error"> · ⚠ la última actualización falló: {q.error}</span>}
        </p>
      </section>

      {signal.error && !signal.data
        ? <section className="card"><p className="error">{signal.error}</p></section>
        : <SignalCard s={signal.data} loading={signal.loading} />}

      <section className="card" aria-labelledby="chart-title">
        <div className="card-head">
          <h2 id="chart-title">Precio e indicadores</h2>
          <PeriodPicker value={period} onChange={choose} />
        </div>
        {hist.error && <p className="error">{hist.error}</p>}
        {!hist.data && !hist.error && <p className="muted">Cargando gráfico…</p>}
        {hist.data && (
          <PriceChart bars={hist.data.bars} currency={currency} colors={colors} resetKey={`${symbol}-${period}`} />
        )}
      </section>

      <section className="card" aria-labelledby="score-title">
        <div className="card-head">
          <h2 id="score-title">Evolución de la señal combinada</h2>
          <span className="muted small">sobre la línea de compra = COMPRAR; bajo la de venta = VENDER</span>
        </div>
        {scores.error && <p className="error">{scores.error}</p>}
        {scores.data && (
          <ScoreChart points={scores.data.points} colors={colors} thresholds={signal.data?.thresholds ?? { buy: 30, sell: -30 }} />
        )}
      </section>

      <section className="card" aria-labelledby="metrics-title">
        <div className="card-head">
          <h2 id="metrics-title">Métricas</h2>
          {ind.data && <span className="muted small">{ind.data.history_days} días de historia</span>}
        </div>
        {ind.error && <p className="error">{ind.error}</p>}
        {ind.data && <Metrics data={ind.data} currency={currency} />}
      </section>

      <BacktestPanel symbol={symbol} colors={colors} />
    </div>
  )
}
