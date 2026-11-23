// Tipos de las respuestas del backend (analisis/backend/app/api/routes.py).

export type FreshnessKind = 'tiempo_real' | 'retraso' | 'cierre' | 'desactualizado' | 'diario' | 'sin_datos'
export type Label = 'COMPRAR' | 'MANTENER' | 'VENDER'

export interface Freshness {
  kind: FreshnessKind
  label: string
  as_of_local: string | null
  age_minutes: number | null
}

export interface QuoteView {
  symbol: string
  name: string | null
  market: 'CL' | 'US' | 'FX'
  currency: string | null
  price: number | null
  prev_close: number | null
  change_pct: number | null
  source: string | null
  as_of: string | null
  fetched_at: string | null
  status: 'ok' | 'error' | 'sin_datos'
  error: string | null
  freshness: Freshness
}

export interface StrategyView {
  key: string
  label: string
  score: number | null
  weight: number
  reasons: string[]
}

export interface Signal {
  symbol: string
  date?: string
  label: Label | null
  score?: number | null
  confidence?: number | null
  provisional?: boolean
  summary: string
  reasons: string[]
  strategies: StrategyView[]
  volume_factor?: number
  actions?: { holding: string; not_holding: string }
  warnings?: string[]
  stop_reference?: number | null
  thresholds?: { buy: number; sell: number }
  price?: Pick<QuoteView, 'price' | 'currency' | 'change_pct' | 'freshness'>
  previous?: { date: string; label: Label; score: number } | null
  changed?: boolean
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  sma20: number | null
  sma50: number | null
  sma200: number | null
  ema20: number | null
  ema50: number | null
  ema200: number | null
  rsi14: number | null
  macd: number | null
  macd_signal: number | null
  macd_hist: number | null
  bb_upper: number | null
  bb_mid: number | null
  bb_lower: number | null
  atr14: number | null
  rel_volume: number | null
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

export interface IndicatorsResponse {
  symbol: string
  quote: QuoteView
  bar_date: string
  provisional: boolean
  indicators: Record<string, number | null>
  metrics: Metrics
  history_days: number
}

export interface ScorePoint {
  date: string
  composite: number | null
  label: Label | null
  [strategy: string]: number | string | null
}

export interface MarketInfo {
  market: string
  name: string
  open: boolean
  closes_at?: string
  next_open?: string
}

export interface ProviderStatus {
  name: string
  configured: boolean
  markets: string[]
  realtime_markets: string[]
  last_ok: string | null
  last_error: string | null
  cooling_down_s: number
}

export interface Status {
  disclaimer: string
  now: string
  timezone: string
  markets: MarketInfo[]
  providers: ProviderStatus[]
  poll_seconds: number
  last_cycle: string | null
  last_cycle_error: string | null
}

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

export interface EquityPoint {
  date: string
  value: number
}

export interface BtStrategy {
  key: string
  label: string
  metrics: BtMetrics
  vs_buy_hold: { return_diff_pct: number; drawdown_diff_pct: number; verdict: string }
  equity: EquityPoint[]
  trades: { entry_date: string; entry_price: number; exit_date: string | null; exit_price: number | null; return_pct: number; open: boolean }[]
}

export interface BacktestResult {
  symbol: string
  start: string
  end: string
  years: number
  adjusted_prices: boolean
  assumptions: {
    execution: string
    position: string
    commission_pct: number
    slippage_pct: number
    thresholds: { buy: number; sell: number }
  }
  buy_hold: { metrics: BtMetrics; equity: EquityPoint[] }
  strategies: BtStrategy[]
  warning: string
}
