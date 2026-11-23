import { ColorType, CrosshairMode, LineSeries, createChart, type MouseEventParams, type Time } from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api } from '../api'
import { day, num, pct, signClass } from '../format'
import type { ChartColors } from '../hooks'
import type { BacktestResult, BtMetrics, EquityPoint } from '../types'

interface Line {
  key: string
  label: string
  color: string
  width: 1 | 2 | 3
  data: EquityPoint[]
}

function EquityChart({ lines, visible, colors }: { lines: Line[]; visible: Set<string>; colors: ChartColors }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<string | null>(null)
  const shown = useMemo(() => lines.filter((l) => visible.has(l.key)), [lines, visible])
  const maps = useMemo(() => new Map(shown.map((l) => [l.key, new Map(l.data.map((p) => [p.date, p.value]))])), [shown])

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const chart = createChart(box, {
      height: 280,
      width: box.clientWidth,
      layout: { background: { type: ColorType.Solid, color: colors.surface }, textColor: colors.muted, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.line },
      timeScale: { borderColor: colors.line },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: 'es-CL', priceFormatter: (v: number) => num(v, 0) },
    })
    for (const l of shown) {
      chart.addSeries(LineSeries, {
        color: l.color, lineWidth: l.width, priceLineVisible: false, lastValueVisible: true, title: l.label,
      }).setData(l.data.map((p) => ({ time: p.date as Time, value: p.value * 100 })))
    }
    chart.timeScale().fitContent()
    const onMove = (param: MouseEventParams<Time>) => setHover(param.time ? (param.time as string) : null)
    chart.subscribeCrosshairMove(onMove)
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth }))
    ro.observe(box)
    return () => {
      ro.disconnect()
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
    }
  }, [shown, colors])

  const date = hover ?? shown[0]?.data[shown[0].data.length - 1]?.date
  return (
    <div className="chart-box">
      {date && (
        <div className="readout num">
          <b>{day(date)}</b>
          {shown.map((l) => (
            <span key={l.key}>
              <span className="swatch" style={{ background: l.color, marginRight: 4 }} aria-hidden="true" />
              <span className="k">{l.label}</span> {num((maps.get(l.key)?.get(date) ?? NaN) * 100, 0)}
            </span>
          ))}
        </div>
      )}
      <div ref={boxRef} />
    </div>
  )
}

function Row({ label, m, bh, cls }: { label: string; m: BtMetrics; bh?: BtMetrics; cls?: string }) {
  const diff = bh ? m.total_return_pct - bh.total_return_pct : null
  return (
    <tr className={cls}>
      <td>{label}</td>
      <td className={`num ${signClass(m.total_return_pct)}`}>{pct(m.total_return_pct)}</td>
      <td className="num">{pct(m.cagr_pct)}</td>
      <td className="num">{pct(m.max_drawdown_pct)}</td>
      <td className="num">{m.trades}</td>
      <td className="num">{m.win_rate_pct == null ? '—' : pct(m.win_rate_pct, 0, false)}</td>
      <td className="num">{pct(m.exposure_pct, 0, false)}</td>
      <td className={`num ${signClass(diff)}`}>{diff == null ? '—' : pct(diff)}</td>
    </tr>
  )
}

export function BacktestPanel({ symbol, colors }: { symbol: string; colors: ChartColors }) {
  const [years, setYears] = useState(10)
  const [commission, setCommission] = useState(0.5)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [visible, setVisible] = useState<Set<string>>(new Set(['bh', 'composite']))

  useEffect(() => {
    setResult(null)
    setError(null)
  }, [symbol])

  async function run(e?: FormEvent) {
    e?.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setResult(await api.backtest({ symbol, years, commission_pct: commission }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  // Colores fijos por estrategia (no por posición): la combinada siempre azul, buy & hold siempre en tinta.
  const lines: Line[] = useMemo(() => {
    const STRAT_COLOR: Record<string, string> = {
      composite: colors['series-1'], trend: colors['series-2'], rsi: colors['series-3'],
      macd: colors['series-4'], bollinger: colors['series-5'],
    }
    return result
    ? [
        { key: 'bh', label: 'Buy & hold', color: colors.ink, width: 2, data: result.buy_hold.equity },
        ...['composite', 'trend', 'rsi', 'macd', 'bollinger']
          .map((k) => result.strategies.find((s) => s.key === k))
          .filter((s): s is NonNullable<typeof s> => !!s)
          .map((s) => ({ key: s.key, label: s.label, color: STRAT_COLOR[s.key], width: (s.key === 'composite' ? 3 : 2) as 2 | 3, data: s.equity })),
      ]
    : []
  }, [result, colors])

  return (
    <section className="card" aria-labelledby="bt-title">
      <div className="card-head">
        <h2 id="bt-title">Backtest · {symbol}</h2>
      </div>
      <form className="bt-form" onSubmit={run}>
        <label>
          Años
          <select className="input" value={years} onChange={(e) => setYears(Number(e.target.value))}>
            {[1, 2, 3, 5, 10].map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <label>
          Comisión por operación (%)
          <input className="input" type="number" min={0} max={5} step={0.05} value={commission}
                 onChange={(e) => setCommission(Number(e.target.value))} />
        </label>
        <button className="btn primary" disabled={busy}>{busy ? 'Calculando…' : 'Ejecutar backtest'}</button>
      </form>
      <p className="muted small" style={{ margin: '8px 0 0' }}>
        Solo largo, todo dentro o fuera. La señal del cierre se ejecuta en la apertura siguiente. Se compara con
        comprar y mantener desde el mismo día.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {result && (
        <>
          <p className="small" style={{ margin: '10px 0 0' }}>
            {day(result.start)} → {day(result.end)} ({num(result.years, 1)} años)
            {result.adjusted_prices ? ' · precios ajustados por dividendos' : ''} · comisión {num(result.assumptions.commission_pct, 2)} %
          </p>
          <div className="table-wrap">
            <table className="bt">
              <thead>
                <tr>
                  <th>Estrategia</th><th>Retorno</th><th>Anual</th><th>Caída máx.</th><th>Operaciones</th>
                  <th>Ganadoras</th><th>Invertido</th><th>vs. B&amp;H</th>
                </tr>
              </thead>
              <tbody>
                <Row label="Buy & hold" m={result.buy_hold.metrics} cls="ref" />
                {result.strategies.map((s) => (
                  <Row key={s.key} label={s.label} m={s.metrics} bh={result.buy_hold.metrics} cls={s.key === 'composite' ? 'ref' : ''} />
                ))}
              </tbody>
            </table>
          </div>
          <h3 style={{ margin: '16px 0 6px' }}>Valor de 100 invertidos</h3>
          <div className="legend" role="group" aria-label="Series visibles">
            {lines.map((l) => (
              <label key={l.key}>
                <input
                  type="checkbox"
                  checked={visible.has(l.key)}
                  onChange={() => setVisible((v) => {
                    const n = new Set(v)
                    if (n.has(l.key)) n.delete(l.key)
                    else n.add(l.key)
                    return n
                  })}
                />
                <span className="swatch" style={{ background: l.color, height: l.width + 1 }} aria-hidden="true" />
                {l.label}
              </label>
            ))}
          </div>
          <EquityChart lines={lines} visible={visible} colors={colors} />
          <ul className="verdicts">
            {result.strategies.map((s) => <li key={s.key}><b>{s.label}:</b> {s.vs_buy_hold.verdict}</li>)}
          </ul>
          <p className="warn" style={{ marginTop: 10 }}>⚠ {result.warning}</p>
        </>
      )}
    </section>
  )
}
