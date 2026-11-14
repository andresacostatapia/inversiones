import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { brokers, seedAlerts, seedAssets, seedEvents, seedQuotes, seedSettings, seedTrades } from './data/seed'
import { fingerprint } from './lib/csv'
import { describeRule } from './lib/format'
import { buildLedger, totals, valuePositions, type Ledger, type PositionView, type Totals } from './lib/portfolio'
import { saveData, type Session } from './lib/vault'
import type { AlertEvent, AlertRule, Asset, Currency, ImportBatch, NewTrade, Quote, Settings, Trade } from './types'

export interface State {
  demo: boolean // true = datos de ejemplo (precios simulados)
  assets: Asset[]
  trades: Trade[]
  batches: ImportBatch[]
  alerts: AlertRule[]
  events: AlertEvent[]
  quotes: Record<string, Quote>
  settings: Settings
}

export const demoState = (): State => ({
  demo: true,
  assets: seedAssets,
  trades: seedTrades,
  batches: [],
  alerts: seedAlerts,
  events: seedEvents,
  quotes: seedQuotes,
  settings: seedSettings,
})

/** Portafolio vacío: solo el dólar, que se necesita para convertir monedas. */
export const emptyState = (): State => ({
  demo: false,
  assets: seedAssets.filter((a) => a.id === 'USDCLP'),
  trades: [],
  batches: [],
  alerts: [],
  events: [],
  quotes: { USDCLP: { assetId: 'USDCLP', price: 950, prevClose: 950, asOf: new Date().toISOString() } },
  settings: seedSettings,
})

/** Completa con valores por defecto lo que falte en datos guardados por versiones anteriores. */
export function hydrate(data: unknown): State {
  const d = (data ?? {}) as Partial<State>
  return { ...emptyState(), ...d, settings: { ...seedSettings, ...d.settings } }
}

export const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8)

/** Operaciones de seguridad que provee la pantalla de bloqueo (Gate). */
export interface Security {
  bioSupported: boolean
  bioEnabled: boolean
  lock: () => void
  changePin: (oldPin: string, newPin: string) => Promise<void>
  enableBio: () => Promise<void>
  disableBio: () => Promise<void>
  backup: () => Promise<void>
  restore: (text: string) => Promise<void>
  wipe: () => Promise<void>
}

interface Ctx {
  state: State
  assets: Asset[]
  cur: Currency
  other: Currency
  fx: number
  ledger: Ledger
  views: PositionView[]
  tot: Totals
  security: Security
  toast: string
  notify: (msg: string) => void
  asset: (id: string) => Asset | undefined
  brokerName: (id: string) => string
  addTrades: (list: NewTrade[], batch?: ImportBatch) => void
  deleteTrade: (id: string) => void
  addAsset: (asset: Asset, price: number) => void
  setPrice: (assetId: string, price: number) => void
  saveAlert: (rule: AlertRule) => void
  deleteAlert: (id: string) => void
  setSettings: (patch: Partial<Settings>) => void
  refresh: () => void
  replaceAll: (next: State) => void
}

const AppCtx = createContext<Ctx | null>(null)
export const useApp = () => useContext(AppCtx)!

export function AppProvider({ initial, session, security, children }: { initial: State; session: Session; security: Security; children: ReactNode }) {
  const [state, setState] = useState<State>(initial)
  const [toast, setToast] = useState('')
  const notify = (msg: string) => setToast(msg)

  // Cada cambio se cifra y se guarda. El primer render no: es lo que se acaba de leer.
  const loaded = useRef(false)
  useEffect(() => {
    if (!loaded.current) {
      loaded.current = true
      return
    }
    saveData(session, state).catch(() => notify('No se pudo guardar el último cambio. Revisa el espacio disponible.'))
  }, [state, session])

  useEffect(() => {
    if (!toast) return
    const id = setTimeout(() => setToast(''), 5000)
    return () => clearTimeout(id)
  }, [toast])

  useEffect(() => {
    const t = state.settings.theme
    if (t === 'auto') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = t === 'oscuro' ? 'dark' : 'light'
  }, [state.settings.theme])

  // Bloqueo automático al volver a la app después de tenerla en segundo plano.
  const autoLock = state.settings.autoLockMin
  useEffect(() => {
    if (autoLock < 0) return
    let hiddenAt = 0
    const on = () => {
      if (document.hidden) hiddenAt = Date.now()
      else if (hiddenAt && Date.now() - hiddenAt >= autoLock * 60_000) security.lock()
    }
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  })

  const fx = state.quotes.USDCLP?.price ?? 950
  const ledger = useMemo(() => buildLedger(state.trades, state.assets), [state.trades, state.assets])
  const views = useMemo(() => valuePositions(ledger, state.quotes, fx), [ledger, state.quotes, fx])
  const tot = useMemo(() => totals(views, ledger), [views, ledger])

  /**
   * Aún no hay proveedor de cotizaciones (fase 2). Con datos de ejemplo se simula un
   * movimiento al azar; con datos reales los precios NO se tocan. En ambos casos se
   * evalúan las alertas de precio y de variación diaria.
   */
  const refresh = () => {
    const now = new Date().toISOString()
    let quotes = state.quotes
    if (state.demo) {
      quotes = {}
      for (const q of Object.values(state.quotes)) {
        const price = q.price * (1 + (Math.random() - 0.5) * 0.008)
        quotes[q.assetId] = { ...q, price: Number(price.toFixed(q.price < 1000 ? 2 : 0)), asOf: now }
      }
    } else notify('Aún no hay cotizaciones automáticas. Por ahora actualiza cada precio a mano en el detalle del activo.')

    const events = [...state.events]
    for (const r of state.alerts) {
      const q = quotes[r.assetId]
      const a = state.assets.find((x) => x.id === r.assetId)
      if (r.muted || !q || !a) continue
      const dayPct = (q.price / q.prevClose - 1) * 100
      const hit =
        (r.kind === 'precio_sobre' && q.price > (r.level ?? Infinity)) ||
        (r.kind === 'precio_bajo' && q.price < (r.level ?? -Infinity)) ||
        (r.kind === 'variacion_dia' && Math.abs(dayPct) >= (r.pct ?? Infinity))
      const already = events.some((e) => e.ruleId === r.id && e.triggeredAt.slice(0, 10) === now.slice(0, 10))
      if (hit && !already)
        events.unshift({
          id: uid(),
          ruleId: r.id,
          assetId: r.assetId,
          triggeredAt: now,
          value: r.kind === 'variacion_dia' ? dayPct : q.price,
          message: describeRule(r, a.currency),
        })
    }
    if (quotes !== state.quotes || events.length !== state.events.length) setState((s) => ({ ...s, quotes, events }))
  }

  const autoRefresh = state.demo ? state.settings.autoRefreshMin : 0
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(refresh, autoRefresh * 60_000)
    return () => clearInterval(id)
  })

  const ctx: Ctx = {
    state,
    assets: state.assets,
    cur: state.settings.displayCurrency,
    other: state.settings.displayCurrency === 'CLP' ? 'USD' : 'CLP',
    fx,
    ledger,
    views,
    tot,
    security,
    toast,
    notify,
    asset: (id) => state.assets.find((a) => a.id === id),
    brokerName: (id) => brokers.find((b) => b.id === id)?.name ?? id,
    addTrades: (list, batch) =>
      setState((s) => ({
        ...s,
        trades: [...s.trades, ...list.map((t) => ({ ...t, id: uid(), fingerprint: fingerprint(t) }))],
        batches: batch ? [...s.batches, batch] : s.batches,
      })),
    deleteTrade: (id) => setState((s) => ({ ...s, trades: s.trades.filter((t) => t.id !== id) })),
    addAsset: (asset, price) =>
      setState((s) => ({
        ...s,
        assets: [...s.assets, asset],
        quotes: { ...s.quotes, [asset.id]: { assetId: asset.id, price, prevClose: price, asOf: new Date().toISOString() } },
      })),
    // Precio manual: no hay cierre anterior confiable, así que la variación del día queda en 0.
    setPrice: (assetId, price) =>
      setState((s) => ({
        ...s,
        quotes: { ...s.quotes, [assetId]: { assetId, price, prevClose: price, asOf: new Date().toISOString() } },
      })),
    saveAlert: (rule) =>
      setState((s) => ({
        ...s,
        alerts: s.alerts.some((a) => a.id === rule.id) ? s.alerts.map((a) => (a.id === rule.id ? rule : a)) : [rule, ...s.alerts],
      })),
    deleteAlert: (id) => setState((s) => ({ ...s, alerts: s.alerts.filter((a) => a.id !== id) })),
    setSettings: (patch) => setState((s) => ({ ...s, settings: { ...s.settings, ...patch } })),
    refresh,
    replaceAll: (next) => setState(next),
  }

  return <AppCtx.Provider value={ctx}>{children}</AppCtx.Provider>
}
