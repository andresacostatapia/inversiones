import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { compact, day, num, pct, price } from '../format'
import type { ChartColors } from '../hooks'
import type { Bar } from '../types'

type Toggle = 'sma20' | 'sma50' | 'sma200' | 'bb' | 'volume' | 'rsi' | 'macd'
const DEFAULT: Record<Toggle, boolean> = { sma20: true, sma50: true, sma200: true, bb: false, volume: true, rsi: true, macd: true }
const PRICE_H = 340
const PANE_H = 110

interface Props {
  bars: Bar[]
  currency: string | null
  colors: ChartColors
  resetKey: string // símbolo + periodo: si cambia, se vuelve a encuadrar
}

function loadToggles(): Record<Toggle, boolean> {
  try {
    return { ...DEFAULT, ...JSON.parse(localStorage.getItem('chart-toggles') ?? '{}') }
  } catch {
    return DEFAULT
  }
}

export function PriceChart({ bars, currency, colors, resetKey }: Props) {
  const boxRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const rangeRef = useRef<{ key: string; range: LogicalRange | null }>({ key: '', range: null })
  const [on, setOn] = useState(loadToggles)
  const [hover, setHover] = useState<Bar | null>(null)
  const [paneTops, setPaneTops] = useState<{ label: string; top: number }[]>([])
  const byDate = useMemo(() => new Map(bars.map((b) => [b.date, b])), [bars])

  function toggle(k: Toggle) {
    setOn((prev) => {
      const next = { ...prev, [k]: !prev[k] }
      try {
        localStorage.setItem('chart-toggles', JSON.stringify(next))
      } catch {
        /* sin almacenamiento */
      }
      return next
    })
  }

  useEffect(() => {
    const box = boxRef.current
    if (!box || !bars.length) return
    const panes: string[] = []
    if (on.volume) panes.push('Volumen')
    if (on.rsi) panes.push('RSI 14')
    if (on.macd) panes.push('MACD 12-26-9')
    const height = PRICE_H + panes.length * PANE_H

    const fmt = (v: number) => price(v, currency)
    const chart = createChart(box, {
      height,
      autoSize: false,
      width: box.clientWidth,
      layout: {
        background: { type: ColorType.Solid, color: colors.surface },
        textColor: colors.muted,
        fontSize: 11,
        attributionLogo: false,
        panes: { separatorColor: colors.line, separatorHoverColor: colors.line, enableResize: false },
      },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.line },
      timeScale: { borderColor: colors.line, minBarSpacing: 0.5 },
      crosshair: { mode: CrosshairMode.Magnet },
      // Sin priceFormatter global: cada serie trae su formato (precio, volumen, RSI, MACD).
      localization: { locale: 'es-CL' },
    })
    chartRef.current = chart

    const t = (b: Bar) => b.date as Time
    const line = (key: keyof Bar) =>
      bars.filter((b) => b[key] != null).map((b) => ({ time: t(b), value: b[key] as number }))
    const common = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }
    const priceFmt = { type: 'custom' as const, formatter: fmt, minMove: 0.01 }

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: colors.surface, borderUpColor: colors.gain, wickUpColor: colors.gain, // hueca = subió
      downColor: colors.loss, borderDownColor: colors.loss, wickDownColor: colors.loss, // llena = bajó
      priceFormat: priceFmt,
    })
    candles.setData(bars.map((b) => ({ time: t(b), open: b.open, high: b.high, low: b.low, close: b.close })))

    if (on.bb) {
      for (const k of ['bb_upper', 'bb_lower'] as const) {
        chart.addSeries(LineSeries, { ...common, color: colors.band, lineWidth: 1, priceFormat: priceFmt }).setData(line(k))
      }
    }
    const smas: [Toggle, keyof Bar, string, string][] = [
      ['sma20', 'sma20', colors['series-1'], 'SMA20'],
      ['sma50', 'sma50', colors['series-2'], 'SMA50'],
      ['sma200', 'sma200', colors['series-3'], 'SMA200'],
    ]
    for (const [k, key, color, title] of smas) {
      if (on[k]) chart.addSeries(LineSeries, { ...common, color, lineWidth: 2, title, lastValueVisible: true, priceFormat: priceFmt }).setData(line(key))
    }

    let pane = 1
    if (on.volume) {
      const vol = chart.addSeries(HistogramSeries, {
        ...common, color: colors.band, priceFormat: { type: 'custom', formatter: compact, minMove: 1 },
      }, pane++)
      vol.setData(bars.map((b) => ({ time: t(b), value: b.volume })))
    }
    if (on.rsi) {
      const rsi = chart.addSeries(LineSeries, {
        ...common, color: colors['series-1'], lineWidth: 2,
        priceFormat: { type: 'custom', formatter: (v: number) => num(v, 0), minMove: 1 },
        autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
      }, pane++)
      rsi.setData(line('rsi14'))
      rsi.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.08 } })
      for (const [lvl, title] of [[70, 'sobrecompra'], [30, 'sobreventa']] as const) {
        rsi.createPriceLine({ price: lvl, color: colors.muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title })
      }
    }
    if (on.macd) {
      const p = pane++
      const f2 = { type: 'custom' as const, formatter: (v: number) => num(v, 2), minMove: 0.01 }
      const hist = chart.addSeries(HistogramSeries, { ...common, priceFormat: f2 }, p)
      hist.setData(bars.filter((b) => b.macd_hist != null).map((b) => ({
        time: t(b), value: b.macd_hist as number, color: (b.macd_hist as number) >= 0 ? colors['div-pos-fill'] : colors['div-neg-fill'],
      })))
      chart.addSeries(LineSeries, { ...common, color: colors['series-1'], lineWidth: 2, title: 'MACD' }, p).setData(line('macd'))
      chart.addSeries(LineSeries, { ...common, color: colors['series-2'], lineWidth: 2, title: 'señal' }, p).setData(line('macd_signal'))
    }

    // Proporción de alto entre paneles: precio ~3 veces cada panel inferior.
    const ps = chart.panes()
    ps[0]?.setStretchFactor(PRICE_H / PANE_H)
    for (let i = 1; i < ps.length; i++) ps[i].setStretchFactor(1)
    const raf = requestAnimationFrame(() => {
      let top = 0
      const tops: { label: string; top: number }[] = []
      chart.panes().forEach((_, i) => {
        if (i > 0) tops.push({ label: panes[i - 1], top: top + 4 })
        top += chart.paneSize(i).height + 1 // +1: separador entre paneles
      })
      setPaneTops(tops)
    })

    // Mantener el zoom del usuario en las actualizaciones periódicas; reencuadrar al cambiar símbolo/periodo.
    if (rangeRef.current.key === resetKey && rangeRef.current.range) {
      chart.timeScale().setVisibleLogicalRange(rangeRef.current.range)
    } else {
      chart.timeScale().fitContent()
    }
    const saveRange = (r: LogicalRange | null) => {
      rangeRef.current = { key: resetKey, range: r }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(saveRange)

    const onMove = (param: MouseEventParams<Time>) => {
      setHover(param.time ? byDate.get(param.time as string) ?? null : null)
    }
    chart.subscribeCrosshairMove(onMove)

    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth }))
    ro.observe(box)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(saveRange)
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
      chartRef.current = null
    }
  }, [bars, on, colors, currency, resetKey, byDate])

  const b = hover ?? bars[bars.length - 1]
  const prev = b ? bars[bars.indexOf(b) - 1] : undefined
  const chg = b && prev ? (b.close / prev.close - 1) * 100 : null
  const p = (v: number | null | undefined) => price(v ?? null, currency)
  const swatches: [Toggle, string, string][] = [
    ['sma20', 'SMA 20', colors['series-1']],
    ['sma50', 'SMA 50', colors['series-2']],
    ['sma200', 'SMA 200', colors['series-3']],
    ['bb', 'Bollinger', colors.band],
  ]

  return (
    <div>
      <div className="chart-tools">
        <div className="toggles" role="group" aria-label="Indicadores sobre el precio">
          {swatches.map(([k, label, color]) => (
            <label key={k}>
              <input type="checkbox" checked={on[k]} onChange={() => toggle(k)} />
              <span className="swatch" style={{ background: color }} aria-hidden="true" />
              {label}
            </label>
          ))}
        </div>
        <div className="toggles" role="group" aria-label="Paneles">
          {([['volume', 'Volumen'], ['rsi', 'RSI'], ['macd', 'MACD']] as [Toggle, string][]).map(([k, label]) => (
            <label key={k}>
              <input type="checkbox" checked={on[k]} onChange={() => toggle(k)} />
              {label}
            </label>
          ))}
        </div>
      </div>
      <div className="chart-box">
        {b && (
          <div className="readout num" aria-live="off">
            <b>{day(b.date)}</b>
            <span><span className="k">A</span> {p(b.open)}</span>
            <span><span className="k">M</span> {p(b.high)}</span>
            <span><span className="k">m</span> {p(b.low)}</span>
            <span><span className="k">C</span> <b>{p(b.close)}</b> <span className={chg == null ? '' : chg >= 0 ? 'gain' : 'loss'}>{pct(chg, 2)}</span></span>
            {on.sma20 && <span><span className="k">SMA20</span> {p(b.sma20)}</span>}
            {on.sma50 && <span><span className="k">SMA50</span> {p(b.sma50)}</span>}
            {on.sma200 && <span><span className="k">SMA200</span> {p(b.sma200)}</span>}
            {on.bb && <span><span className="k">Bollinger</span> {p(b.bb_lower)}–{p(b.bb_upper)}</span>}
            {on.volume && <span><span className="k">Vol.</span> {compact(b.volume)}{b.rel_volume != null ? ` (${num(b.rel_volume, 1)}x)` : ''}</span>}
            {on.rsi && <span><span className="k">RSI</span> {num(b.rsi14, 0)}</span>}
            {on.macd && <span><span className="k">MACD</span> {num(b.macd, 2)} / {num(b.macd_signal, 2)}</span>}
          </div>
        )}
        {paneTops.map((pt) => (
          <span key={pt.label} className="pane-label" style={{ top: pt.top }}>{pt.label}</span>
        ))}
        <div ref={boxRef} />
      </div>
      <p className="muted small" style={{ margin: '6px 0 0' }}>
        Vela hueca = cerró al alza; llena = a la baja. A/M/m/C: apertura, máximo, mínimo, cierre.
      </p>
    </div>
  )
}
