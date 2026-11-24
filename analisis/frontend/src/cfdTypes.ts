// Tipos del copiloto CFD (respuestas de /api/cfd/…).

export type CfdTf = '1m' | '5m' | '15m' | '1h' | '4h'
export type CfdAllTf = CfdTf | '1d'
export type CfdVerdict = 'COMPRA FUERTE' | 'COMPRA' | 'NEUTRAL' | 'VENTA' | 'VENTA FUERTE' | 'SIN DATOS'

export interface CfdTicket {
  ok: boolean
  actionable: boolean
  asset?: string
  direction?: 'LARGO' | 'CORTO'
  side?: string
  calibrated?: boolean
  entry?: number
  sl?: number
  tp?: number
  sl_points?: number
  tp_points?: number
  atr?: number
  lots?: number
  risk_budget_usd?: number
  risk_usd?: number
  risk_pct_real?: number
  reward_usd?: number
  rr?: number
  value_per_point?: number
  spread_points?: number
  min_lot?: number
  min_lot_risk_usd?: number
  notional_usd?: number
  margin_usd?: number
  margin_pct?: number
  usd_per_point?: number
  warnings: string[]
  error?: string
}

export interface CfdCalibration {
  state: 'sin calibrar' | 'pendiente' | 'ok' | 'antigua' | 'error'
  text: string
  offset: number | null
  xtb_price?: number
  at?: string
  manual?: boolean
  source?: string
  source_name?: string
}

export interface CfdBias {
  score: number | null
  bias: 'alcista' | 'bajista' | 'neutral' | 'sin datos'
  reason: string | null
}

export interface CfdRule {
  id: string
  group: string
  label: string
  detail: string
  value: number
  weight: number
  points: number
  direction: 'alcista' | 'bajista' | 'neutral'
  available: boolean
  agrees: boolean
}

export interface CfdRow {
  code: string
  name: string
  group: 'indice' | 'metal'
  tf: CfdTf
  tf_label: string
  feed_symbol: string | null
  source: string | null
  source_name: string | null
  feed_price: number | null
  offset: number | null
  xtb_price: number | null
  digits: number
  change_pct: number | null
  last_bar: string | null
  delay_min: number | null
  market_open: boolean
  score: number | null
  base_score: number | null
  verdict: CfdVerdict
  candle_time: string | null
  atr: number | null
  atr_pct: number | null
  bias: Record<CfdAllTf, CfdBias>
  ticket: CfdTicket
  calibration: CfdCalibration
  errors: string[]
  notes: string[]
  reason: string | null
}

export interface CfdRegime {
  tf: string
  state: 'risk-on' | 'risk-off' | 'mixto' | 'sin datos'
  label: string
  risk_score: number | null
  metals_score: number | null
  breadth: 'amplia' | 'débil' | 'sin datos'
  detail: string
  scores: Record<string, number | null>
}

export interface CfdContract {
  value_per_point: number
  min_lot: number
  lot_step: number
  digits: number
  margin_pct: number
  spread_points: number
  yahoo: string
  twelvedata: string | null
}

export interface CfdSettings {
  capital_usd: number
  risk_pct: number
  sl_atr: number
  tp_atr: number
  timeframe: CfdTf
  alerts_enabled: boolean
  alert_threshold: number
  signal_threshold: number
  assets: Record<string, Partial<Pick<CfdContract, 'value_per_point' | 'spread_points' | 'margin_pct'>>>
}

export interface CfdOverview {
  disclaimer: string
  tf: CfdTf
  tf_label: string
  now: string
  market_open: boolean
  regime: CfdRegime
  assets: CfdRow[]
  settings: CfdSettings
  contracts: Record<string, CfdContract>
  feed: {
    twelvedata_configured: boolean
    twelvedata_usage: { last_minute: number; per_minute: number | null; last_day: number; per_day: number | null }
    twelvedata_last_error: string | null
  }
  alerts: { telegram: boolean; last_scan: string | null; last_scan_error: string | null }
  timeframes: CfdTf[]
}

export interface CfdChartBar {
  t: number
  open: number
  high: number
  low: number
  close: number
  ema20: number | null
  ema50: number | null
  ema200: number | null
}

export interface CfdDetail extends CfdRow {
  disclaimer: string
  rules: CfdRule[]
  regime: CfdRegime
  contract: CfdContract
  chart: { bars: CfdChartBar[]; source: string | null; calibrated: boolean; forming_from: string | null }
  mtf: { tf: CfdAllTf; weight: number; label: string }[]
}
