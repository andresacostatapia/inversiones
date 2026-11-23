import {
  BaselineSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { day, num } from '../format'
import type { ChartColors } from '../hooks'
import type { ScorePoint } from '../types'

const NAMES: Record<string, string> = { trend: 'Tendencia', rsi: 'RSI', macd: 'MACD', bollinger: 'Bollinger' }

export function ScoreChart({ points, colors, thresholds }: {
  points: ScorePoint[]
  colors: ChartColors
  thresholds: { buy: number; sell: number }
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<ScorePoint | null>(null)
  const byDate = useMemo(() => new Map(points.map((p) => [p.date, p])), [points])

  useEffect(() => {
    const box = boxRef.current
    if (!box || !points.length) return
    const chart = createChart(box, {
      height: 200,
      width: box.clientWidth,
      layout: { background: { type: ColorType.Solid, color: colors.surface }, textColor: colors.muted, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.line, scaleMargins: { top: 0.06, bottom: 0.06 } },
      timeScale: { borderColor: colors.line },
      crosshair: { mode: CrosshairMode.Magnet },
      localization: { locale: 'es-CL', priceFormatter: (v: number) => num(v, 0) },
    })
    const s = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: colors['div-pos'], topFillColor1: colors['div-pos-fill'], topFillColor2: colors['div-pos-fill'],
      bottomLineColor: colors['div-neg'], bottomFillColor1: colors['div-neg-fill'], bottomFillColor2: colors['div-neg-fill'],
      lineWidth: 2, priceLineVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: -100, maxValue: 100 } }),
    })
    s.setData(points.filter((p) => p.composite != null).map((p) => ({ time: p.date as Time, value: p.composite as number })))
    s.createPriceLine({ price: thresholds.buy, color: colors['div-pos'], lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'COMPRAR' })
    s.createPriceLine({ price: thresholds.sell, color: colors['div-neg'], lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'VENDER' })
    chart.timeScale().fitContent()
    const onMove = (param: MouseEventParams<Time>) => setHover(param.time ? byDate.get(param.time as string) ?? null : null)
    chart.subscribeCrosshairMove(onMove)
    const ro = new ResizeObserver(() => chart.applyOptions({ width: box.clientWidth }))
    ro.observe(box)
    return () => {
      ro.disconnect()
      chart.unsubscribeCrosshairMove(onMove)
      chart.remove()
    }
  }, [points, colors, thresholds, byDate])

  const p = hover ?? points[points.length - 1]
  return (
    <div className="chart-box">
      {p && (
        <div className="readout num">
          <b>{day(p.date)}</b>
          <span><span className="k">Combinada</span> <b>{num(p.composite as number | null, 0)}</b> {p.label ?? ''}</span>
          {Object.keys(NAMES).filter((k) => k in p).map((k) => (
            <span key={k}><span className="k">{NAMES[k]}</span> {num(p[k] as number | null, 0)}</span>
          ))}
        </div>
      )}
      <div ref={boxRef} />
    </div>
  )
}
