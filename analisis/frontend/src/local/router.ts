// Atiende en el teléfono las mismas rutas que la API REST del servidor (/api/...), con el backend local.
// Además: actualización periódica mientras la app está abierta y notificaciones de las alertas importantes.
import { App as CapApp } from '@capacitor/app'
import { LocalNotifications } from '@capacitor/local-notifications'
import { ApiError, LocalBackend } from './backend'
import { isNative } from './net'

const TICK_MS = 15_000
export const UPDATED_EVENT = 'analisis:actualizado'
let backendP: Promise<LocalBackend> | null = null

async function setupNotifications(b: LocalBackend) {
  if (!isNative()) return
  try {
    const perm = await LocalNotifications.checkPermissions()
    const ok = perm.display === 'granted' || (await LocalNotifications.requestPermissions()).display === 'granted'
    if (!ok) return
    let next = 1
    b.onNewAlert = (a) => {
      void LocalNotifications.schedule({
        notifications: [{ id: next++, title: a.title, body: a.message, extra: { symbol: a.symbol } }],
      })
    }
  } catch {
    /* sin permiso o sin el plugin: las alertas quedan en la pestaña Alertas */
  }
}

export function localBackend(): Promise<LocalBackend> {
  if (!backendP) {
    backendP = (async () => {
      const b = new LocalBackend()
      await b.init()
      await setupNotifications(b)
      // Al terminar cada actualización se avisa al dashboard para que recargue sin esperar su próximo ciclo.
      const notify = () => {
        if (typeof window !== 'undefined') window.dispatchEvent(new Event(UPDATED_EVENT))
      }
      const tick = () => void b.cycle().then(notify, notify)
      tick()
      setInterval(() => {
        if (typeof document === 'undefined' || document.visibilityState === 'visible') tick()
      }, TICK_MS)
      if (isNative()) {
        void CapApp.addListener('appStateChange', ({ isActive }) => {
          if (isActive) tick()
        })
      }
      return b
    })()
  }
  return backendP
}

/** Misma firma que la llamada HTTP: ruta relativa a /api y método. */
export async function localRequest(path: string, init?: RequestInit): Promise<unknown> {
  const b = await localBackend()
  const method = (init?.method ?? 'GET').toUpperCase()
  const url = new URL(path, 'http://local')
  const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent)
  const q = (k: string) => url.searchParams.get(k) ?? undefined
  const body = init?.body ? JSON.parse(String(init.body)) : {}
  const [a, s, c] = parts
  try {
    if (method === 'GET') {
      if (a === 'status') return b.status()
      if (a === 'watchlist') return b.watchlist()
      if (a === 'fx') return b.fx()
      if (a === 'quotes' && s) return b.quoteView(s.toUpperCase())
      if (a === 'signals' && !s) return b.signals()
      if (a === 'signals' && s && c === 'history') return b.scoreHistory(s, q('period') ?? '1y')
      if (a === 'signals' && s) return b.signal(s)
      if (a === 'instruments' && s && c === 'history') return b.history(s, q('period') ?? '1y')
      if (a === 'instruments' && s && c === 'indicators') return b.indicators(s)
      if (a === 'portfolio') return b.portfolio()
      if (a === 'transactions') return b.transactions()
      if (a === 'alerts') return b.alerts()
      if (a === 'profile') return b.profile()
      if (a === 'search') return b.search(q('q') ?? '')
    }
    if (method === 'POST') {
      if (a === 'watchlist') return b.addSymbol(body.symbol)
      if (a === 'refresh') return b.refresh()
      if (a === 'backtest') return b.backtest(body)
      if (a === 'transactions' && s === 'import') return b.importCsv(body.csv, !!body.commit)
      if (a === 'transactions') return b.addTransaction(body)
      if (a === 'contribution') return b.contribution(body.amount_clp)
      if (a === 'alerts' && s === 'read-all') return b.readAllAlerts()
      if (a === 'alerts' && s === 'test') return b.testAlert()
      if (a === 'alerts' && s && c === 'read') return b.readAlert(Number(s))
    }
    if (method === 'DELETE') {
      if (a === 'watchlist' && s) return b.removeSymbol(s)
      if (a === 'transactions' && s) return b.deleteTransaction(Number(s))
    }
  } catch (e) {
    if (e instanceof ApiError) throw e
    throw new ApiError(502, (e as Error).message)
  }
  throw new ApiError(404, `ruta no disponible en la app: ${method} ${path}`)
}
