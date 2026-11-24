import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CfdChartBar } from '../cfdTypes'
import { num } from '../format'
import type { ChartColors } from '../hooks'

interface Props {
  bars: CfdChartBar[]
  digits: number
  colors: ChartColors
  levels: { entry?: number; sl?: number; tp?: number } | null
  resetKey: string // activo + temporalidad: si cambia, se vuelve a encuadrar
  tz?: string
  intraday: boolean
}

/** Velas del CFD (en precio de xStation si está calibrado) con EMA20/50/200 y las líneas de entrada, SL y TP. */
export function CfdChart({ bars, digits, colors, levels, resetKey, tz = 'America/Santiago', intraday }: Props) {
  const boxRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const rangeRef = useRef<{ key: string; range: LogicalRange | null }>({ key: '', range: null })
  const [hover, setHover] = useState<CfdChartBar | null>(null)
  const byTime = useMemo(() => new Map(bars.map((b) => [b.t, b])), [bars])
  const fmtTime = useMemo(() => {
    const full = new Intl.DateTimeFormat('es-CL', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: tz,
    })
    const short = new Intl.DateTimeFormat('es-CL', intraday
      ? { hour: '2-digit', minute: '2-digit', timeZone: tz }
      : { day: '2-digit', month: 'short', timeZone: tz })
    return { full: (t: number) => full.format(new Date(t * 1000)), short: (t: number) => short.format(new Date(t * 1000)) }
  }, [tz, intraday])

  useEffect(() => {
    const box = boxRef.current
    if (!box || !bars.length) return
    const fmt = (v: number) => num(v, digits)
    const chart = createChart(box, {
      height: 380,
      width: box.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: colors.surface },
        textColor: colors.muted,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.line },
      timeScale: {
        borderColor: colors.line, timeVisible: intraday, secondsVisible: false,
        tickMarkFormatter: (t: Time) => fmtTime.short(t as number),
      },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: 'es-CL', timeFormatter: (t: Time) => fmtTime.full(t as number) },
    })
    chartRef.current = chart
    const t = (b: CfdChartBar) => b.t as UTCTimestamp
    const priceFmt = { type: 'custom' as const, formatter: fmt, minMove: 1 / 10 ** digits }
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: colors.surface, borderUpColor: colors.gain, wickUpColor: colors.gain,
      downColor: colors.loss, borderDownColor: colors.loss, wickDownColor: colors.loss,
      priceFormat: priceFmt,
    })
    candles.setData(bars.map((b) => ({ time: t(b), open: b.open, high: b.high, low: b.low, close: b.close })))
    const common = { priceLineVisible: false, crosshairMarkerVisible: false, priceFormat: priceFmt }
    const emas: [keyof CfdChartBar, string, string][] = [
      ['ema20', colors['series-1'], 'EMA20'], ['ema50', colors['series-2'], 'EMA50'], ['ema200', colors['series-3'], 'EMA200'],
    ]
    for (const [k, color, title] of emas) {
      chart.addSeries(LineSeries, { ...common, color, lineWidth: 2, title, lastValueVisible: false })
        .setData(bars.filter((b) => b[k] != null).map((b) => ({ time: t(b), value: b[k] as number })))
    }
    if (levels) {
      const lines: [number | undefined, string, string, LineStyle][] = [
        [levels.entry, colors.ink, 'Entrada', LineStyle.Solid],
        [levels.sl, colors.loss, 'SL', LineStyle.Dashed],
        [levels.tp, colors.gain, 'TP', LineStyle.Dashed],
      ]
      for (const [p, color, title, lineStyle] of lines) {
        if (p != null && Number.isFinite(p)) candles.createPriceLine({ price: p, color, lineWidth: 2, lineStyle, title, axisLabelVisible: true })
      }
    }
    if (rangeRef.current.key === resetKey && rangeRef.current.range) {
      chart.timeScale().setVisibleLogicalRange(rangeRef.current.range)
    } else {
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 120), to: bars.length + 4 })
    }
    const saveRange = (r: LogicalRange | null) => {
      rangeRef.current = { key: resetKey, range: r }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(saveRange)
    const onMove = (param: MouseEventParams<Time>) => setHover(param.time ? byTime.get(param.time as number) ?? null : null)
    chart.subscribeCrosshairMove(onMove)
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth }))
    ro.observe(box)
    return () => {
      ro.disconnect()
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(saveRange)
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
    }
  }, [bars, colors, digits, levels, resetKey, byTime, fmtTime, intraday])

  const b = hover ?? bars[bars.length - 1]
  const p = (v: number | null | undefined) => num(v ?? null, digits)
  return (
    <div>
      <div className="chart-tools">
        <div className="toggles" aria-label="Leyenda">
          {([['EMA 20', colors['series-1']], ['EMA 50', colors['series-2']], ['EMA 200', colors['series-3']]] as const).map(([l, c]) => (
            <span key={l} className="legend-item"><span className="swatch" style={{ background: c }} aria-hidden="true" />{l}</span>
          ))}
          {levels && <span className="legend-item"><span className="swatch" style={{ background: colors.ink }} aria-hidden="true" />Entrada</span>}
          {levels && <span className="legend-item"><span className="swatch dashed" style={{ borderColor: colors.loss }} aria-hidden="true" />SL</span>}
          {levels && <span className="legend-item"><span className="swatch dashed" style={{ borderColor: colors.gain }} aria-hidden="true" />TP</span>}
        </div>
      </div>
      <div className="chart-box">
        {b && (
          <div className="readout num" aria-live="off">
            <b>{fmtTime.full(b.t)}</b>
            <span><span className="k">A</span> {p(b.open)}</span>
            <span><span className="k">M</span> {p(b.high)}</span>
            <span><span className="k">m</span> {p(b.low)}</span>
            <span><span className="k">C</span> <b>{p(b.close)}</b></span>
            <span><span className="k">EMA20</span> {p(b.ema20)}</span>
            <span><span className="k">EMA50</span> {p(b.ema50)}</span>
            <span><span className="k">EMA200</span> {p(b.ema200)}</span>
          </div>
        )}
        <div ref={boxRef} />
      </div>
    </div>
  )
}
