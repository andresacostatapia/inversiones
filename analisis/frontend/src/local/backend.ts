// Backend local de la app Android: hace en el teléfono lo mismo que el servidor de Python y devuelve las MISMAS
// respuestas que la API REST, para que el dashboard no cambie. Sin servidor, sin PC: Yahoo y mindicador directo.
import { runBacktest, type BacktestConfig } from '../core/backtest'
import { num, pct } from '../core/fmt'
import { basicMetrics, computeAll, type Bars } from '../core/indicators'
import { freshness, isOpen, marketOf, sessionInfo, type Freshness } from '../core/market'
import { buildPositions, bucketOf, InvalidTransaction, investability, suggestContribution, valuePortfolio, type Candidate, type PriceInfo, type Tx } from '../core/portfolio'
import { KIND_LABELS } from '../core/catalog'
import { evaluate, scoreFrame, scoreHistory, type Label, type SignalsConfig } from '../core/signals'
import { fingerprint, mapHeaders, parseCsv, parseDate, parseNum, parseSide } from './csv'
import { Db } from './db'
import { getJson, mindicadorBase } from './net'
import { getHistory, getQuote, RateLimited, search as yahooSearch, SymbolNotFound, type Quote } from './yahoo'

export const DISCLAIMER = 'Herramienta educativa, no es asesoría financiera.'
const PAIR = 'USDCLP'
const SYMBOL_RE = /^[A-Z0-9^][A-Z0-9.\-=^]{0,19}$/ // ^ al inicio: índices (^GSPC)
const PERIODS: Record<string, number | null> = { '1m': 31, '3m': 92, '6m': 183, '1y': 366, '2y': 731, '5y': 1827, '10y': 3653, max: null }
const DAY = 864e5

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export interface Settings {
  timezone: string
  poll_seconds: number
  closed_poll_seconds: number
  stale_after_minutes: number
  history_years: number
  signals: SignalsConfig
  backtest: BacktestConfig
  risk_profile: {
    target_allocation: Record<string, number>
    review_drawdown_pct: number
    sell_drawdown_pct: number
    monthly_contribution_clp: number
    fractional_shares: Record<string, boolean>
    allow_derivatives?: boolean
    allow_leverage?: boolean
    allow_short?: boolean
  }
  alerts: { signal_changes: boolean; data_error_minutes: number; notify_min_severity: string }
}

// Valores iniciales = analisis/config.yaml
export const DEFAULT_SETTINGS: Settings = {
  timezone: 'America/Santiago', poll_seconds: 60, closed_poll_seconds: 3600, stale_after_minutes: 30, history_years: 10,
  signals: { thresholds: { buy: 30, sell: -30 }, weights: { trend: 1, rsi: 1, macd: 1, bollinger: 1 }, volume_effect: 0.2 },
  backtest: { commission_pct: 0.5, slippage_pct: 0, default_years: 10 },
  risk_profile: { target_allocation: { CL: 0.3, INTL: 0.7 }, review_drawdown_pct: -20, sell_drawdown_pct: -25,
    monthly_contribution_clp: 500000, fractional_shares: { CL: false, US: true },
    allow_derivatives: false, allow_leverage: false, allow_short: false },
  alerts: { signal_changes: true, data_error_minutes: 30, notify_min_severity: 'warning' },
}
export const DEFAULT_WATCHLIST = ['SQM-B.SN', 'CHILE.SN', 'SPY', 'VOO', 'EEM', 'VWO']

interface QuoteRow extends Partial<Quote> {
  symbol: string
  status: 'ok' | 'error' | 'sin_datos'
  error: string | null
  error_at: string | null
}

interface BarsRow extends Bars {
  official_last: string | null // última vela de la historia oficial (la de hoy puede venir del precio en vivo)
}

interface SignalRow {
  symbol: string
  date: string
  label: Label | null
  score: number | null
  confidence: number | null
  provisional: boolean
  summary: string
}

interface TxRow extends Tx {
  id?: number
  fx_source: 'manual' | 'historico' | 'actual'
  note: string | null
  created_at: string
}

export interface AlertRow {
  id?: number
  key: string
  kind: string
  severity: 'info' | 'warning' | 'critical'
  symbol: string | null
  title: string
  message: string
  value: number | null
  created_at: string
  resolved_at: string | null
  read_at: string | null
  notified: string | null
  notify_error: string | null
}

const iso = (d: Date) => d.toISOString()
const dayNum = (d: string) => Date.parse(`${d}T00:00:00Z`) / DAY
const isoDay = (n: number) => new Date(n * DAY).toISOString().slice(0, 10)
const clean = (v: unknown) => (typeof v === 'number' && !Number.isFinite(v) ? null : v)

export function normalizeSymbol(raw: string): string {
  const s = (raw ?? '').trim().toUpperCase()
  if (!SYMBOL_RE.test(s)) throw new ApiError(422, `símbolo inválido: '${raw}'`)
  return s
}

export interface CycleResult {
  quotes: Record<string, boolean>
  fx: boolean
  history: string[]
  signals: string[]
  alerts: string[]
}

export class LocalBackend {
  private lastRefresh = new Map<string, number>()
  private lastHistory = new Map<string, number>()
  private wasOpen = new Map<string, boolean>()
  private lastCycle: Date | null = null
  private lastCycleError: string | null = null
  private fxErrors: Record<string, string> = {}
  private fxHistoryChecked = false
  private running: Promise<CycleResult> | null = null
  // Si Yahoo limita las consultas (429), se pausa y se reintenta más tarde: 5, 10, 20, 30 minutos.
  private yahooPausedUntil = 0
  private yahooStrikes = 0
  settings: Settings = DEFAULT_SETTINGS
  /** La app conecta aquí las notificaciones del teléfono. */
  onNewAlert: ((a: ReturnType<typeof alertDict>) => void) | null = null

  constructor(readonly db = new Db(), private clock: () => Date = () => new Date()) {}

  async init() {
    const s = await this.db.get<Settings>('kv', 'settings')
    // Valores nuevos de versiones posteriores se completan con los por defecto.
    this.settings = s ? { ...DEFAULT_SETTINGS, ...s, risk_profile: { ...DEFAULT_SETTINGS.risk_profile, ...s.risk_profile } } : DEFAULT_SETTINGS
    if (!s) await this.db.put('kv', DEFAULT_SETTINGS, 'settings')
    if (!(await this.db.get('kv', 'watchlist'))) await this.db.put('kv', DEFAULT_WATCHLIST, 'watchlist')
  }

  // --- watchlist ------------------------------------------------------------------------
  async watchlistSymbols(): Promise<string[]> {
    return (await this.db.get<string[]>('kv', 'watchlist')) ?? []
  }

  async watchlist() {
    return Promise.all((await this.watchlistSymbols()).map((s) => this.quoteView(s)))
  }

  async addSymbol(raw: string) {
    const symbol = normalizeSymbol(raw)
    let q: Quote
    try {
      q = await this.yahoo(() => getQuote(symbol))
    } catch (e) {
      if (e instanceof SymbolNotFound) {
        throw new ApiError(404, `No hay datos para ${symbol}. Revisa el símbolo (Chile usa el sufijo .SN, p. ej. SQM-B.SN). Detalle: ${e.message}`)
      }
      throw new ApiError(502, `No se pudo validar ${symbol}: el proveedor no respondió (${(e as Error).message})`)
    }
    await this.storeQuote(q)
    const wl = await this.watchlistSymbols()
    if (!wl.includes(symbol)) await this.db.put('kv', [...wl, symbol], 'watchlist')
    try {
      await this.refreshHistory(symbol)
    } catch {
      /* el precio ya quedó; la historia se reintenta en el próximo ciclo */
    }
    return this.quoteView(symbol)
  }

  async removeSymbol(raw: string) {
    const symbol = normalizeSymbol(raw)
    const wl = await this.watchlistSymbols()
    if (!wl.includes(symbol)) throw new ApiError(404, `${symbol} no está en la watchlist`)
    await this.db.put('kv', wl.filter((s) => s !== symbol), 'watchlist')
    return { removed: symbol }
  }

  // --- precios --------------------------------------------------------------------------
  private async storeQuote(q: Quote) {
    await this.db.put<QuoteRow>('quotes', { ...q, status: 'ok', error: null, error_at: null })
    if (marketOf(q.symbol) !== 'FX') await this.updateTodayBar(q)
    this.lastRefresh.set(q.symbol, this.clock().getTime())
  }

  private async updateTodayBar(q: Quote) {
    const rec = await this.db.get<BarsRow>('bars', q.symbol)
    if (!rec) return // sin historia todavía: la trae refreshHistory
    const tz = q.tz ?? (marketOf(q.symbol) === 'CL' ? 'America/Santiago' : 'America/New_York')
    const d = new Date(q.as_of).toLocaleDateString('sv-SE', { timeZone: tz })
    const n = rec.date.length
    if (n && rec.date[n - 1] === d) {
      const i = n - 1
      rec.close[i] = q.price
      rec.high[i] = Math.max(rec.high[i], q.day_high ?? q.price, q.price)
      rec.low[i] = Math.min(rec.low[i], q.day_low ?? q.price, q.price)
      if (q.volume) rec.volume[i] = q.volume
      if (rec.adj_close) rec.adj_close[i] = null
    } else if (!n || rec.date[n - 1] < d) {
      const o = q.day_open ?? q.price
      rec.date.push(d); rec.open.push(o); rec.high.push(Math.max(o, q.price)); rec.low.push(Math.min(o, q.price))
      rec.close.push(q.price); rec.volume.push(q.volume ?? 0); rec.adj_close?.push(null)
    }
    await this.db.put('bars', rec, q.symbol)
  }

  private async storeError(symbol: string, err: unknown) {
    const row = (await this.db.get<QuoteRow>('quotes', symbol)) ?? { symbol, status: 'error', error: null, error_at: null }
    row.status = err instanceof SymbolNotFound ? 'sin_datos' : 'error'
    row.error = (err as Error).message
    row.error_at = iso(this.clock())
    await this.db.put('quotes', row)
    this.lastRefresh.set(symbol, this.clock().getTime())
  }

  /** Llama a Yahoo respetando la pausa por límite de consultas. */
  private async yahoo<T>(fn: () => Promise<T>): Promise<T> {
    const now = this.clock().getTime()
    if (now < this.yahooPausedUntil) {
      const at = new Date(this.yahooPausedUntil).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit', timeZone: this.settings.timezone })
      throw new RateLimited(`Yahoo limitó las consultas; se reintenta a las ${at}`)
    }
    try {
      const r = await fn()
      this.yahooStrikes = 0
      return r
    } catch (e) {
      if (e instanceof RateLimited) {
        this.yahooStrikes++
        this.yahooPausedUntil = now + Math.min(30, 5 * 2 ** (this.yahooStrikes - 1)) * 60e3
      }
      throw e
    }
  }

  async refreshQuote(symbol: string): Promise<boolean> {
    try {
      await this.storeQuote(await this.yahoo(() => getQuote(symbol)))
      return true
    } catch (e) {
      await this.storeError(symbol, e)
      return false
    }
  }

  async quoteView(symbol: string, now = this.clock()) {
    const q = await this.db.get<QuoteRow>('quotes', symbol)
    const market = marketOf(symbol)
    const asOf = q?.as_of ? new Date(q.as_of) : null
    const fresh = freshness(asOf, {
      realtime: !!q?.realtime, marketOpen: isOpen(market, now), now, staleAfterMinutes: this.settings.stale_after_minutes,
      tz: this.settings.timezone, daily: !!q?.source?.includes('observado'), hoursKnown: market !== 'OTHER',
    })
    const price = q?.price ?? null
    const prev = q?.prev_close ?? null
    return {
      symbol, name: q?.name ?? null, market, kind: q?.kind ?? null, currency: q?.currency ?? null, price, prev_close: prev,
      change_pct: price && prev ? (price / prev - 1) * 100 : null, source: q?.source ?? null,
      as_of: q?.as_of ?? null, fetched_at: q?.fetched_at ?? null, status: q?.status ?? 'sin_datos', error: q?.error ?? null,
      freshness: fresh,
    }
  }

  // --- historia --------------------------------------------------------------------------
  async refreshHistory(symbol: string): Promise<number> {
    const rec = await this.db.get<BarsRow>('bars', symbol)
    if (!rec || !rec.official_last) {
      const b = await this.yahoo(() => getHistory(symbol, { years: this.settings.history_years }))
      await this.db.put<BarsRow>('bars', { ...b, official_last: b.date[b.date.length - 1] ?? null }, symbol)
      return b.date.length
    }
    const start = isoDay(dayNum(rec.official_last) - 7)
    const b = await this.yahoo(() => getHistory(symbol, { start }))
    const keep = rec.date.findIndex((d) => d >= start)
    const head = keep < 0 ? rec.date.length : keep
    const merged: BarsRow = {
      date: [...rec.date.slice(0, head), ...b.date], open: [...rec.open.slice(0, head), ...b.open],
      high: [...rec.high.slice(0, head), ...b.high], low: [...rec.low.slice(0, head), ...b.low],
      close: [...rec.close.slice(0, head), ...b.close], volume: [...rec.volume.slice(0, head), ...b.volume],
      adj_close: [...(rec.adj_close ?? []).slice(0, head), ...(b.adj_close ?? b.date.map(() => null))],
      official_last: b.date[b.date.length - 1] ?? rec.official_last,
    }
    await this.db.put('bars', merged, symbol)
    return b.date.length
  }

  async bars(symbol: string): Promise<Bars | null> {
    const rec = await this.db.get<BarsRow>('bars', symbol)
    return rec && rec.date.length ? rec : null
  }

  // --- dólar -----------------------------------------------------------------------------
  async refreshFx() {
    let q: Quote | null = null
    try {
      const data = await getJson<{ serie: { fecha: string; valor: number }[] }>(`${mindicadorBase()}/api/dolar`, 10_000)
      const s = data.serie ?? []
      if (!s.length) throw new Error('sin datos del dólar')
      delete this.fxErrors.mindicador
      q = { symbol: PAIR, price: s[0].valor, prev_close: s[1]?.valor ?? null, currency: 'CLP', as_of: new Date(s[0].fecha).toISOString(),
        fetched_at: iso(this.clock()), source: 'mindicador (observado)', realtime: false, name: 'Dólar (CLP por USD)',
        exchange: null, day_open: null, day_high: null, day_low: null, volume: null }
      await this.storeFxDay(s[0].fecha.slice(0, 10), s[0].valor, 'observado')
    } catch (e) {
      this.fxErrors.mindicador = `mindicador: ${(e as Error).message}`
      try {
        const y = await this.yahoo(() => getQuote('CLP=X'))
        q = { ...y, symbol: PAIR, source: 'yahoo (mercado)', name: 'Dólar (CLP por USD)' }
        const d = new Date(y.as_of).toLocaleDateString('sv-SE', { timeZone: 'America/Santiago' })
        await this.storeFxDay(d, y.price, 'mercado')
      } catch (e2) {
        await this.storeError(PAIR, e2)
        return null
      }
    }
    await this.storeQuote(q)
    return q
  }

  private async fxTable(): Promise<Record<string, [number, string]>> {
    return (await this.db.get<Record<string, [number, string]>>('kv', 'fx')) ?? {}
  }

  private async storeFxDay(d: string, rate: number, kind: string) {
    const t = await this.fxTable()
    if (!t[d] || kind === 'observado' || t[d][1] !== 'observado') t[d] = [rate, kind]
    await this.db.put('kv', t, 'fx')
  }

  async refreshFxHistory() {
    const b = await this.yahoo(() => getHistory('CLP=X', { years: this.settings.history_years }))
    const t = await this.fxTable()
    b.date.forEach((d, i) => {
      if (!t[d]) t[d] = [b.close[i], 'mercado']
    })
    await this.db.put('kv', t, 'fx')
    return b.date.length
  }

  async fxOn(d: string): Promise<number | null> {
    const t = await this.fxTable()
    const best = Object.keys(t).filter((k) => k <= d).sort().pop()
    return best ? t[best][0] : null
  }

  async fx() {
    return this.quoteView(PAIR)
  }

  // --- ciclo de actualización ---------------------------------------------------------------
  /** Una pasada: precios que tocan, dólar, historia, señales y alertas. `force` pide todo ahora. */
  cycle(force = false) {
    if (!this.running) {
      this.running = this.runCycle(force).finally(() => {
        this.running = null
      })
    }
    return this.running
  }

  private async runCycle(force: boolean): Promise<CycleResult> {
    const now = this.clock()
    try {
      const symbols = await this.watchlistSymbols()
      if (force) this.lastRefresh.clear()
      const due = [...symbols, PAIR].filter((s) => {
        const last = this.lastRefresh.get(s)
        const interval = isOpen(marketOf(s), now) ? this.settings.poll_seconds : this.settings.closed_poll_seconds
        return last === undefined || (now.getTime() - last) / 1000 >= interval
      })
      const results: Record<string, boolean> = {}
      for (const s of due.filter((x) => x !== PAIR)) results[s] = await this.refreshQuote(s)
      const fxDue = due.includes(PAIR)
      if (fxDue) await this.refreshFx()

      const historyDone: string[] = []
      for (const s of symbols) {
        const m = marketOf(s)
        const open = isOpen(m, now)
        const last = this.lastHistory.get(s)
        if (force || last === undefined || (this.wasOpen.get(m) && !open) || now.getTime() - last >= 12 * 3600e3) {
          try {
            await this.refreshHistory(s)
            historyDone.push(s)
          } catch {
            /* se reintenta en el próximo plazo */
          }
          this.lastHistory.set(s, now.getTime())
        }
      }
      const signalsDone: string[] = []
      for (const s of new Set([...Object.keys(results).filter((k) => results[k]), ...historyDone])) {
        try {
          await this.signal(s)
          signalsDone.push(s)
        } catch {
          /* sin historia todavía */
        }
      }
      if (!this.fxHistoryChecked) {
        try {
          if (Object.keys(await this.fxTable()).length < 200) await this.refreshFxHistory()
          this.fxHistoryChecked = true
        } catch {
          /* se reintenta en el próximo ciclo */
        }
      }
      const newAlerts = await this.evaluateAlerts(now)
      for (const s of symbols) this.wasOpen.set(marketOf(s), isOpen(marketOf(s), now))
      this.lastCycle = now
      this.lastCycleError = null
      return { quotes: results, fx: fxDue, history: historyDone, signals: signalsDone, alerts: newAlerts.map((a) => a.title) }
    } catch (e) {
      this.lastCycleError = (e as Error).message
      throw e
    }
  }

  refresh() {
    return this.cycle(true)
  }

  status() {
    const now = this.clock()
    return {
      disclaimer: DISCLAIMER, now: iso(now), timezone: this.settings.timezone,
      markets: (['CL', 'US', 'FX'] as const).map((m) => sessionInfo(m, now)),
      providers: [{ name: 'yahoo', configured: true, markets: ['CL', 'FX', 'US'], realtime_markets: [], last_ok: null,
        last_error: null, cooling_down_s: Math.max(0, Math.round((this.yahooPausedUntil - now.getTime()) / 1000)) }],
      fx_sources_errors: this.fxErrors, poll_seconds: this.settings.poll_seconds,
      last_cycle: this.lastCycle ? iso(this.lastCycle) : null, last_cycle_error: this.lastCycleError,
    }
  }

  // --- señales ---------------------------------------------------------------------------------
  private async needBars(symbol: string): Promise<Bars> {
    let b = await this.bars(symbol)
    // Primer arranque: la descarga de la historia puede estar en curso; mejor esperarla que responder "sin datos".
    if (!b && this.running) {
      await this.running.catch(() => undefined)
      b = await this.bars(symbol)
    }
    if (!b && (await this.watchlistSymbols()).includes(symbol)) {
      try {
        await this.refreshHistory(symbol)
      } catch {
        /* se informa abajo */
      }
      b = await this.bars(symbol)
    }
    if (!b) throw new ApiError(404, `Sin historia guardada para ${symbol}. ¿Está en la watchlist? Prueba Actualizar.`)
    return b
  }

  async signal(rawSymbol: string, store = true) {
    const symbol = normalizeSymbol(rawSymbol)
    const bars = await this.needBars(symbol)
    const quote = await this.quoteView(symbol)
    const kind = quote.freshness.kind
    const df = computeAll(bars)
    const last = bars.date[bars.date.length - 1]
    const sessionDay = quote.freshness.as_of_local?.slice(0, 10) ?? '0000-00-00'
    const provisional = ['tiempo_real', 'retraso', 'desactualizado'].includes(kind) && last >= sessionDay
    const cfg = this.settings.signals
    const res = evaluate(symbol, df, bars.date, cfg, { provisional, stale: kind === 'desactualizado', frame: scoreFrame(df, cfg, provisional) })
    for (const [a, b] of findGaps(bars.date)) {
      res.warnings.push(`Faltan datos reales entre el ${dmy(a)} y el ${dmy(b)} (el proveedor no informó precios): `
        + 'las medias y el backtest de ese tramo son menos confiables')
    }
    const prev = await this.previousSignal(symbol, res.date)
    const out = {
      ...res, price: { price: quote.price, currency: quote.currency, change_pct: quote.change_pct, freshness: quote.freshness },
      previous: prev, changed: !!(prev && res.label && prev.label !== res.label),
    }
    if (store && res.label) {
      await this.db.put<SignalRow>('signals', { symbol, date: res.date, label: res.label, score: res.score, confidence: res.confidence,
        provisional: res.provisional, summary: res.summary })
    }
    return { disclaimer: DISCLAIMER, ...out }
  }

  private async signalRows(symbol: string): Promise<SignalRow[]> {
    return (await this.db.all<SignalRow>('signals')).filter((r) => r.symbol === symbol).sort((a, b) => (a.date < b.date ? -1 : 1))
  }

  private async previousSignal(symbol: string, day: string) {
    const rows = (await this.signalRows(symbol)).filter((r) => r.date < day)
    const r = rows[rows.length - 1]
    return r ? { date: r.date, label: r.label, score: r.score } : null
  }

  async signals() {
    const out = []
    for (const s of await this.watchlistSymbols()) {
      try {
        out.push(await this.signal(s))
      } catch (e) {
        out.push({ symbol: s, label: null, summary: (e as Error).message, reasons: [], strategies: [] })
      }
    }
    return { disclaimer: DISCLAIMER, signals: out }
  }

  async scoreHistory(symbol: string, period: string) {
    const bars = await this.needBars(normalizeSymbol(symbol))
    let pts = scoreHistory(bars, this.settings.signals)
    const days = PERIODS[period]
    if (days) {
      const cut = dayNum(bars.date[bars.date.length - 1]) - days
      pts = pts.filter((p) => dayNum(p.date) >= cut)
    }
    return { symbol, period, points: pts }
  }

  async history(rawSymbol: string, period: string) {
    const symbol = normalizeSymbol(rawSymbol)
    const bars = await this.needBars(symbol)
    const df = computeAll(bars)
    const days = PERIODS[period]
    const cut = days ? dayNum(bars.date[bars.date.length - 1]) - days : -Infinity
    const cols = Object.keys(df)
    const rows = []
    for (let i = 0; i < bars.date.length; i++) {
      if (dayNum(bars.date[i]) < cut) continue
      const r: Record<string, unknown> = { date: bars.date[i] }
      for (const c of cols) r[c] = clean(df[c][i])
      r.adj_close = bars.adj_close?.[i] ?? null
      rows.push(r)
    }
    return { symbol, period, bars: rows }
  }

  async indicators(rawSymbol: string) {
    const symbol = normalizeSymbol(rawSymbol)
    const bars = await this.needBars(symbol)
    const df = computeAll(bars)
    const n = bars.date.length
    const snap: Record<string, number | null> = {}
    for (const [k, s] of Object.entries(df)) snap[k] = clean(s[n - 1]) as number | null
    const quote = await this.quoteView(symbol)
    return {
      symbol, quote, bar_date: bars.date[n - 1], provisional: !['cierre', 'sin_datos'].includes(quote.freshness.kind),
      indicators: snap, metrics: basicMetrics(bars.close), history_days: n,
    }
  }

  async backtest(body: { symbol: string; years?: number; commission_pct?: number; slippage_pct?: number }) {
    const symbol = normalizeSymbol(body.symbol)
    const bars = await this.needBars(symbol)
    const bt = { ...this.settings.backtest, ...(body.commission_pct !== undefined ? { commission_pct: body.commission_pct } : {}),
      ...(body.slippage_pct !== undefined ? { slippage_pct: body.slippage_pct } : {}) }
    try {
      return runBacktest(symbol, bars, this.settings.signals, bt, { years: body.years ?? bt.default_years })
    } catch (e) {
      throw new ApiError(422, (e as Error).message)
    }
  }

  // --- cartera ------------------------------------------------------------------------------
  private async txRows(): Promise<TxRow[]> {
    return this.db.all<TxRow>('transactions')
  }

  async transactions() {
    return (await this.txRows())
      .sort((a, b) => (a.date === b.date ? (b.id ?? 0) - (a.id ?? 0) : a.date < b.date ? 1 : -1))
      .map((t) => ({ ...t, total: t.quantity * t.price + (t.side === 'compra' ? t.fee : -t.fee) }))
  }

  private today() {
    return this.clock().toLocaleDateString('sv-SE', { timeZone: this.settings.timezone })
  }

  async addTransaction(t: { symbol: string; side: string; quantity: number; price: number; date: string; fee?: number;
    fx_rate?: number | null; note?: string | null }) {
    const side = String(t.side).trim().toLowerCase()
    if (side !== 'compra' && side !== 'venta') throw new ApiError(422, "el tipo debe ser 'compra' o 'venta'")
    if (!(t.quantity > 0) || !(t.price > 0)) throw new ApiError(422, 'cantidad y precio deben ser mayores que 0')
    const fee = t.fee ?? 0
    if (fee < 0) throw new ApiError(422, 'la comisión no puede ser negativa')
    if (!parseDate(t.date)) throw new ApiError(422, 'fecha inválida')
    if (t.date > this.today()) throw new ApiError(422, 'la fecha no puede ser futura')
    if (t.fx_rate !== null && t.fx_rate !== undefined && t.fx_rate <= 0) throw new ApiError(422, 'el tipo de cambio debe ser mayor que 0')
    const symbol = normalizeSymbol(t.symbol)
    try {
      buildPositions([...(await this.txRows()), { symbol, side, quantity: t.quantity, price: t.price, currency: '?', date: t.date, fee, fx_rate: 1 }])
    } catch (e) {
      throw new ApiError(422, (e as Error).message)
    }
    if (!(await this.watchlistSymbols()).includes(symbol)) await this.addSymbol(symbol)
    if (side === 'compra') {
      const reason = await this.blocked(symbol)
      if (reason) throw new ApiError(422, `${symbol}: ${reason}`)
    }
    const currency = (await this.quoteView(symbol)).currency ?? (marketOf(symbol) === 'CL' ? 'CLP' : 'USD')
    let fx: number
    let src: TxRow['fx_source']
    if (t.fx_rate) {
      fx = t.fx_rate
      src = 'manual'
    } else {
      const h = await this.fxOn(t.date)
      const cur = (await this.quoteView(PAIR)).price
      if (h) {
        fx = h
        src = 'historico'
      } else if (cur) {
        fx = cur
        src = 'actual'
      } else {
        throw new ApiError(422, 'no hay dato del dólar para esa fecha; ingrésalo a mano (CLP por USD)')
      }
    }
    const row: TxRow = { symbol, side, quantity: t.quantity, price: t.price, currency, date: t.date, fee, fx_rate: fx,
      fx_source: src, note: t.note ?? null, created_at: iso(this.clock()) }
    row.id = (await this.db.add('transactions', row)) as number
    return { ...row, total: row.quantity * row.price + (side === 'compra' ? fee : -fee) }
  }

  async deleteTransaction(id: number) {
    const all = await this.txRows()
    if (!all.some((t) => t.id === id)) throw new ApiError(404, 'Operación no encontrada')
    try {
      buildPositions(all.filter((t) => t.id !== id))
    } catch (e) {
      throw new ApiError(422, `no se puede borrar: una venta posterior quedaría sin respaldo (${(e as Error).message})`)
    }
    await this.db.delete('transactions', id)
    return { deleted: id }
  }

  /** Por qué el perfil no permite comprar el símbolo (índice, futuro, divisa, moneda no soportada, apalancado). */
  private async blocked(symbol: string): Promise<string | null> {
    const q = await this.quoteView(symbol)
    const rp = this.settings.risk_profile
    return investability(symbol, q.kind, q.currency, q.name, !!rp.allow_derivatives, !!rp.allow_leverage, !!rp.allow_short)
  }

  async search(query: string) {
    const q = (query ?? '').trim()
    if (!q) throw new ApiError(422, 'escribe algo para buscar')
    let hits
    try {
      hits = await this.yahoo(() => yahooSearch(q))
    } catch (e) {
      if (e instanceof RateLimited) throw new ApiError(429, e.message)
      throw new ApiError(502, `La búsqueda no respondió: ${(e as Error).message}`)
    }
    const wl = new Set(await this.watchlistSymbols())
    const rp = this.settings.risk_profile
    return {
      query: q,
      results: hits.map((h) => ({ ...h, kind_label: KIND_LABELS[h.kind ?? ''] ?? h.kind ?? '', market: marketOf(h.symbol),
        note: investability(h.symbol, h.kind, null, h.name, !!rp.allow_derivatives, !!rp.allow_leverage, !!rp.allow_short),
        in_watchlist: wl.has(h.symbol.toUpperCase()) })),
    }
  }

  private async priceInfo(symbol: string): Promise<PriceInfo> {
    const q = await this.quoteView(symbol)
    return { price: q.price, prev_close: q.prev_close, currency: q.currency, name: q.name, freshness: q.freshness }
  }

  async portfolio() {
    const rp = this.settings.risk_profile
    let positions
    try {
      positions = buildPositions(await this.txRows())
    } catch (e) {
      throw new ApiError(409, `Las operaciones registradas no cuadran: ${(e as Error).message}`)
    }
    const prices: Record<string, PriceInfo> = {}
    for (const [s, p] of Object.entries(positions)) if (p.quantity > 0) prices[s] = await this.priceInfo(s)
    const fx = await this.quoteView(PAIR)
    const out = valuePortfolio(positions, prices, fx.price, rp.target_allocation, rp.review_drawdown_pct, rp.sell_drawdown_pct)
    return { disclaimer: DISCLAIMER, ...out, fx, monthly_contribution_clp: rp.monthly_contribution_clp }
  }

  async contribution(amount?: number | null) {
    const rp = this.settings.risk_profile
    const value = amount ?? rp.monthly_contribution_clp
    if (!(value > 0)) throw new ApiError(422, 'el aporte debe ser mayor que 0')
    const fx = (await this.quoteView(PAIR)).price
    if (!fx) throw new ApiError(422, 'no hay dato del dólar todavía: espera la primera actualización')
    const pf = await this.portfolio()
    const bucketValues = Object.fromEntries(pf.allocation.map((a) => [a.bucket, a.value_clp]))
    const held = new Map(pf.positions.map((p) => [p.symbol as string, p]))
    const symbols = [...new Set([...[...held.keys()].sort(), ...(await this.watchlistSymbols())])]
    const cands: Candidate[] = []
    for (const s of symbols) {
      const info = await this.priceInfo(s)
      if (!info.price) continue
      const cur = info.currency ?? (marketOf(s) === 'CL' ? 'CLP' : 'USD')
      const rows = await this.signalRows(s)
      const label = rows.length ? rows[rows.length - 1].label : null
      const pos = held.get(s)
      cands.push({ symbol: s, price_native: info.price, currency: cur, price_clp: cur === 'USD' ? info.price * fx : info.price,
        signal: label, held: !!pos, name: info.name ?? null, risk: (pos?.status as string) ?? null,
        drawdown_pct: (pos?.drawdown_pct as number) ?? null, blocked: await this.blocked(s) })
    }
    const res = suggestContribution(value, bucketValues, cands, rp.target_allocation, rp.fractional_shares)
    return { disclaimer: DISCLAIMER, ...res, fx, portfolio_value_clp: Object.values(bucketValues).reduce((a, b) => a + b, 0) }
  }

  async importCsv(text: string, commit: boolean) {
    const rows = parseCsv(text)
    if (rows.length < 2) throw new ApiError(422, 'el archivo no tiene filas de datos')
    const cols = mapHeaders(rows[0])
    const missing = ['date', 'symbol', 'side', 'quantity', 'price'].filter((c) => !(c in cols))
    if (missing.length) throw new ApiError(422, `faltan columnas: ${missing.join(', ')} (encabezados: ${rows[0].join(', ')})`)
    const existing = new Set((await this.txRows()).map((t) => fingerprint(t.date, t.symbol, t.side, t.quantity, t.price)))
    const seen = new Set<string>()
    type Item = { line: number; raw: string[]; status: string; error: string | null; symbol?: string; side?: 'compra' | 'venta';
      quantity?: number; price?: number; date?: string; fee?: number; fx_rate?: number | null; note?: string | null }
    const preview: Item[] = rows.slice(1).map((r, k) => {
      const get = (key: string) => (key in cols && cols[key] < r.length ? r[cols[key]].trim() : '')
      const item: Item = { line: k + 2, raw: r, status: 'lista', error: null }
      try {
        const d = parseDate(get('date'))
        const side = parseSide(get('side'))
        const qty = parseNum(get('quantity'))
        const price = parseNum(get('price'))
        const fee = (parseNum(get('fee')) ?? 0) + (parseNum(get('tax')) ?? 0)
        const fx = parseNum(get('fx'))
        const cur = get('currency').toUpperCase()
        let sym = get('symbol').toUpperCase()
        if (cur === 'CLP' && !sym.includes('.')) sym += '.SN'
        sym = normalizeSymbol(sym)
        if (!d || !side || !qty || !price) throw new Error('fecha, tipo, cantidad o precio inválidos')
        Object.assign(item, { symbol: sym, side, quantity: qty, price, date: d, fee, fx_rate: cur === 'USD' || fx ? fx : null, note: get('note') || null })
        const fp = fingerprint(d, sym, side, qty, price)
        if (existing.has(fp) || seen.has(fp)) item.status = 'duplicada'
        seen.add(fp)
      } catch (e) {
        item.status = 'error'
        item.error = (e as Error).message
      }
      return item
    })
    const ready = preview.filter((p) => p.status === 'lista')
    let consistency: string | null = null
    try {
      buildPositions([...(await this.txRows()), ...ready.map((p) => ({ symbol: p.symbol!, side: p.side!, quantity: p.quantity!,
        price: p.price!, currency: '?', date: p.date!, fee: p.fee!, fx_rate: 1 }))])
    } catch (e) {
      consistency = (e as Error).message
    }
    const result = { rows: preview, ready: ready.length, duplicates: preview.filter((p) => p.status === 'duplicada').length,
      errors: preview.filter((p) => p.status === 'error').length, consistency_error: consistency, imported: 0 }
    if (commit) {
      if (consistency) throw new ApiError(422, `no se importó nada: ${consistency}`)
      const order = [...ready].sort((a, b) => (a.date! === b.date! ? (a.side === 'compra' ? 0 : 1) - (b.side === 'compra' ? 0 : 1) : a.date! < b.date! ? -1 : 1))
      for (const p of order) {
        try {
          await this.addTransaction({ symbol: p.symbol!, side: p.side!, quantity: p.quantity!, price: p.price!, date: p.date!,
            fee: p.fee, fx_rate: p.fx_rate, note: p.note })
          p.status = 'importada'
          result.imported++
        } catch (e) {
          p.status = 'error'
          p.error = (e as Error).message
        }
      }
    }
    return result
  }

  async profile() {
    return this.settings.risk_profile
  }

  // --- alertas -------------------------------------------------------------------------------
  private async alertRows(): Promise<AlertRow[]> {
    return this.db.all<AlertRow>('alerts')
  }

  private async createAlert(a: Omit<AlertRow, 'created_at' | 'resolved_at' | 'read_at' | 'notified' | 'notify_error'>) {
    const row: AlertRow = { ...a, created_at: iso(this.clock()), resolved_at: null, read_at: null, notified: null, notify_error: null }
    const sev = { info: 0, warning: 1, critical: 2 } as const
    const min = sev[this.settings.alerts.notify_min_severity as keyof typeof sev] ?? 1
    if (sev[row.severity] >= min && this.onNewAlert) row.notified = 'telefono'
    row.id = (await this.db.add('alerts', row)) as number
    const d = alertDict(row)
    if (row.notified) this.onNewAlert?.(d)
    return d
  }

  private async condition(active: boolean, a: Omit<AlertRow, 'created_at' | 'resolved_at' | 'read_at' | 'notified' | 'notify_error'>) {
    const rows = await this.alertRows()
    const open = rows.filter((r) => r.key === a.key && !r.resolved_at)
    if (active && !open.length) return this.createAlert(a)
    if (!active && open.length) for (const r of open) await this.db.put('alerts', { ...r, resolved_at: iso(this.clock()) })
    return null
  }

  async evaluateAlerts(now = this.clock()) {
    const created: ReturnType<typeof alertDict>[] = []
    const rp = this.settings.risk_profile
    // Caídas desde la compra.
    let pf: Awaited<ReturnType<LocalBackend['portfolio']>> | null = null
    try {
      pf = await this.portfolio()
    } catch {
      pf = null
    }
    const held = new Set<string>()
    for (const p of pf?.positions ?? []) {
      const sym = p.symbol as string
      const dd = p.drawdown_pct as number | null
      held.add(sym)
      if (dd === null) continue
      const cur = p.currency as string
      const base = `${sym} está ${pct(dd)} bajo tu costo promedio de compra (${num(p.avg_cost as number)} ${cur}; precio actual ${num(p.price as number)} ${cur}).`
      const sell = p.status === 'vender'
      const review = p.status === 'revisar' || p.status === 'vender'
      const a = await this.condition(sell, { key: `venta:${sym}`, kind: 'venta', severity: 'critical', symbol: sym,
        title: `${sym}: caída de ${pct(dd)} — umbral de VENTA (${pct(rp.sell_drawdown_pct, 0)})`,
        message: `${base} Tu perfil de riesgo indica considerar vender.`, value: dd })
      const b = await this.condition(review && !sell, { key: `revision:${sym}`, kind: 'revision', severity: 'warning', symbol: sym,
        title: `${sym}: caída de ${pct(dd)} — revisar la posición (${pct(rp.review_drawdown_pct, 0)})`,
        message: `${base} Revisa si la tesis de inversión sigue en pie.`, value: dd })
      for (const x of [a, b]) if (x) created.push(x)
    }
    for (const r of await this.alertRows()) {
      if (!r.resolved_at && (r.kind === 'venta' || r.kind === 'revision') && !held.has(r.symbol ?? '')) {
        await this.db.put('alerts', { ...r, resolved_at: iso(now) })
      }
    }
    // Cambios de señal.
    if (this.settings.alerts.signal_changes) {
      const all = await this.alertRows()
      for (const sym of await this.watchlistSymbols()) {
        const rows = await this.signalRows(sym)
        if (rows.length < 2) continue
        const cur = rows[rows.length - 1]
        const prev = rows[rows.length - 2]
        if (!cur.label || !prev.label || cur.label === prev.label) continue
        const key = `senal:${sym}:${cur.date}:${cur.label}`
        if (all.some((r) => r.key === key)) continue
        const prov = cur.provisional ? ' (provisional: el mercado sigue abierto)' : ''
        created.push(await this.createAlert({ key, kind: 'senal', severity: cur.label === 'MANTENER' ? 'info' : 'warning', symbol: sym,
          title: `${sym}: la señal cambió de ${prev.label} a ${cur.label}${prov}`,
          message: `${cur.summary ?? ''} Puntaje ${num(cur.score, 0)}, confianza ${num(cur.confidence, 0)} %.`, value: cur.score }))
      }
    }
    // Precios que no llegan con el mercado abierto.
    const limit = this.settings.alerts.data_error_minutes * 60e3
    for (const sym of await this.watchlistSymbols()) {
      const q = await this.db.get<QuoteRow>('quotes', sym)
      const lastOk = q?.fetched_at ? Date.parse(q.fetched_at) : null
      const failing = !!(q && q.status !== 'ok' && isOpen(marketOf(sym), now) && (lastOk === null || now.getTime() - lastOk >= limit))
      const a = await this.condition(failing, { key: `datos:${sym}`, kind: 'datos', severity: 'info', symbol: sym,
        title: `${sym}: sin precios nuevos desde hace más de ${this.settings.alerts.data_error_minutes} min`,
        message: `El proveedor falla: ${q?.error ?? ''}. Se muestra el último precio conocido.`, value: null })
      if (a) created.push(a)
    }
    return created
  }

  async alerts() {
    const rows = (await this.alertRows()).sort((a, b) => (b.id ?? 0) - (a.id ?? 0)).slice(0, 100)
    const unread = (await this.alertRows()).filter((r) => !r.read_at)
    const by: Record<string, number> = {}
    for (const r of unread) by[r.severity] = (by[r.severity] ?? 0) + 1
    return { alerts: rows.map(alertDict), unread: unread.length, by_severity: by, channels: { telegram: false, email: false, phone: !!this.onNewAlert } }
  }

  async readAlert(id: number) {
    const r = (await this.alertRows()).find((x) => x.id === id && !x.read_at)
    if (r) await this.db.put('alerts', { ...r, read_at: iso(this.clock()) })
    return { updated: r ? 1 : 0 }
  }

  async readAllAlerts() {
    let n = 0
    for (const r of await this.alertRows()) {
      if (!r.read_at) {
        await this.db.put('alerts', { ...r, read_at: iso(this.clock()) })
        n++
      }
    }
    return { updated: n }
  }

  async testAlert() {
    const a = await this.createAlert({ key: `prueba:${iso(this.clock())}`, kind: 'prueba', severity: 'warning', symbol: null,
      title: 'Alerta de prueba', message: 'Si ves esta notificación en el teléfono, las alertas funcionan.', value: null })
    return { alert: a, sent: a.notified, errors: [], channels: { phone: !!this.onNewAlert } }
  }
}

export function alertDict(r: AlertRow) {
  return {
    id: r.id!, kind: r.kind, severity: r.severity, symbol: r.symbol, title: r.title, message: r.message, value: r.value,
    created_at: r.created_at, resolved_at: r.resolved_at, read: !!r.read_at,
    notified: r.notified ? r.notified.split(',') : [], notify_error: r.notify_error,
  }
}

/** Huecos de más de 10 días corridos entre velas en el último año (como find_gaps en Python). */
export function findGaps(dates: string[], withinDays = 365, minGap = 10): [string, string][] {
  if (dates.length < 2) return []
  const lastN = dayNum(dates[dates.length - 1])
  const idx = dates.filter((d) => dayNum(d) >= lastN - withinDays)
  const out: [string, string][] = []
  for (let i = 1; i < idx.length; i++) if (dayNum(idx[i]) - dayNum(idx[i - 1]) > minGap) out.push([idx[i - 1], idx[i]])
  return out
}

const dmy = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`

export type { Freshness }
export { bucketOf, InvalidTransaction, RateLimited }
