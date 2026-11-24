import { num, pct, price, signClass } from '../format'
import type { IndicatorsResponse } from '../types'

export function Metrics({ data, currency }: { data: IndicatorsResponse; currency: string | null }) {
  const m = data.metrics
  const i = data.indicators
  const tiles: [string, string, string?][] = [
    ['1 mes', pct(m.return_1m_pct), signClass(m.return_1m_pct)],
    ['3 meses', pct(m.return_3m_pct), signClass(m.return_3m_pct)],
    ['6 meses', pct(m.return_6m_pct), signClass(m.return_6m_pct)],
    ['1 año', pct(m.return_1y_pct), signClass(m.return_1y_pct)],
    ['Volatilidad anual', pct(m.volatility_1y_pct, 1, false)],
    ['Máx. 52 semanas', price(m.high_52w, currency)],
    ['Mín. 52 semanas', price(m.low_52w, currency)],
    ['Desde el máximo', pct(m.from_high_52w_pct), signClass(m.from_high_52w_pct)],
    ['Vs. SMA200', pct(m.dist_sma200_pct), signClass(m.dist_sma200_pct)],
    ['RSI 14', num(i.rsi14, 0)],
    ['ATR 14', `${price(i.atr14, currency)} (${pct(i.atr14_pct, 1, false)})`],
    ['Volumen relativo', i.rel_volume == null ? '—' : `${num(i.rel_volume, 2)}x`],
  ]
  return (
    <div className="tiles">
      {tiles.map(([k, v, cls]) => (
        <div className="tile" key={k}>
          <div className="k">{k}</div>
          <div className={`v num ${cls ?? ''}`}>{v}</div>
        </div>
      ))}
    </div>
  )
}
