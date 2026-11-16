import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { brokers, seedAlerts, seedAssets, seedEvents, seedQuotes, seedSettings, seedTrades } from './data/seed'
import { fingerprint } from './lib/csv'
import { describeRule } from './lib/format'
import { buildLedger, totals, valuePositions, type Ledger, type PositionView, type Totals } from './lib/portfolio'
import { fetchDollar, fetchQuotes, marketOpen, quotesEnabled } from './lib/quotes'
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

/** Revisa las reglas de precio y de variación diaria; devuelve la lista de alertas cumplidas actualizada. */
function evaluateAlerts(s: State, quotes: Record<string, Quote>, now: string): AlertEvent[] {
  const events = [...s.events]
  for (const r of s.alerts) {
    const q = quotes[r.assetId]
    const a = s.assets.find((x) => x.id === r.assetId)
    if (r.muted || !q || !a) continue
    const dayPct = (q.price / q.prevClose - 1) * 100
    const hit =
      (r.kind === 'precio_sobre' && q.price > (r.level ?? Infinity)) ||
      (r.kind === 'precio_bajo' && q.price < (r.level ?? -Infinity)) ||
      (r.kind === 'variacion_dia' && Math.abs(dayPct) >= (r.pct ?? Infinity))
    // Una misma regla avisa a lo más una vez por día.
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
  return events
}

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
  refreshing: boolean
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
  refresh: (silent?: boolean) => Promise<void>
  replaceAll: (next: State) => void
}

const AppCtx = createContext<Ctx | null>(null)
export const useApp = () => useContext(AppCtx)!

export function AppProvider({ initial, session, security, children }: { initial: State; session: Session; security: Security; children: ReactNode }) {
  const [state, setState] = useState<State>(initial)
  const [toast, setToast] = useState('')
  const [refreshing, setRefreshing] = useState(false)
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
   * Actualiza precios y dólar, y revisa las alertas.
   * - Datos de ejemplo: simula un movimiento al azar (nunca se mezclan con precios reales).
   * - Datos reales: dólar observado desde mindicador.cl y precios desde el servidor de
   *   cotizaciones. Lo que no se pueda actualizar conserva su último precio guardado.
   * `silent` = actualización automática: no muestra mensajes.
   */
  const busy = useRef(false)
  const refresh = async (silent = false) => {
    if (busy.current) return
    const now = new Date().toISOString()

    if (state.demo) {
      setState((s) => {
        const quotes: Record<string, Quote> = {}
        for (const q of Object.values(s.quotes)) {
          const price = q.price * (1 + (Math.random() - 0.5) * 0.008)
          quotes[q.assetId] = { ...q, price: Number(price.toFixed(q.price < 1000 ? 2 : 0)), asOf: now }
        }
        return { ...s, quotes, events: evaluateAlerts(s, quotes, now) }
      })
      return
    }

    busy.current = true
    setRefreshing(true)
    try {
      const [dollar, stocks] = await Promise.allSettled([
        fetchDollar(),
        quotesEnabled() ? fetchQuotes(state.assets) : Promise.resolve({ quotes: [], failed: [] }),
      ])
      const fresh: Quote[] = []
      if (dollar.status === 'fulfilled') fresh.push(dollar.value)
      if (stocks.status === 'fulfilled') fresh.push(...stocks.value.quotes)

      setState((s) => {
        const quotes = { ...s.quotes }
        // Solo se aplican precios de activos que siguen existiendo.
        for (const q of fresh) if (s.assets.some((a) => a.id === q.assetId)) quotes[q.assetId] = q
        return { ...s, quotes, events: evaluateAlerts(s, quotes, now) }
      })

      if (!silent) {
        const failed = stocks.status === 'fulfilled' ? stocks.value.failed : []
        if (dollar.status === 'rejected' && stocks.status === 'rejected') notify('Sin conexión. Se muestran los últimos precios guardados.')
        else if (stocks.status === 'rejected') notify('No se pudo consultar el servidor de cotizaciones. Se conservan los últimos precios.')
        else if (!quotesEnabled()) notify('Dólar actualizado. El servidor de cotizaciones aún no está configurado: los precios siguen a mano.')
        else if (failed.length) notify(`Actualizado, salvo: ${failed.join(', ')}. Revisa su símbolo o edita el precio a mano.`)
        else if (dollar.status === 'rejected') notify('Precios actualizados. No se pudo obtener el dólar.')
        else notify('Precios y dólar actualizados.')
      }
    } finally {
      busy.current = false
      setRefreshing(false)
    }
  }

  // La actualización automática siempre usa la versión más reciente de `refresh`.
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  // Al abrir (desbloquear) la app con datos reales, trae precios una vez.
  useEffect(() => {
    if (!initial.demo) refreshRef.current(true)
  }, [initial.demo])

  // Después, cada N minutos mientras la app esté a la vista y algún mercado esté operando.
  const every = state.settings.autoRefreshMin
  const demo = state.demo
  useEffect(() => {
    if (!every) return
    const id = setInterval(() => {
      if (demo || (!document.hidden && marketOpen())) refreshRef.current(true)
    }, every * 60_000)
    return () => clearInterval(id)
  }, [every, demo])

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
    refreshing,
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
