/**
 * Alertas con la app cerrada: la app le entrega al servidor las reglas y la suscripción push
 * del celular; el servidor las revisa cada 5 minutos en horario de mercado y avisa.
 *
 * Al servidor solo viajan símbolo y condición de cada regla. Nunca cantidades ni montos.
 */
import { QUOTES_URL, VAPID_PUBLIC } from '../config'
import type { AlertEvent, AlertRule, Asset } from '../types'
import { describeRule } from './format'
import { quotesEnabled, yahooSymbol } from './quotes'

export class ServerError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function call<T>(key: string, method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(QUOTES_URL + path, {
      method,
      headers: { Authorization: `Bearer ${key}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(20_000),
    })
  } catch {
    throw new ServerError(0, 'Sin conexión con el servidor de alertas.')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ServerError(res.status, data.error ?? `El servidor respondió ${res.status}.`)
  return data as T
}

/** Clave aleatoria que identifica a esta instalación ante el servidor. Vive cifrada junto a los datos. */
export function newDeviceKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32))
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Símbolo con que el servidor vigila un activo; vacío si su precio es manual. */
const watchSymbol = (a: Asset) => (a.id === 'USDCLP' ? 'USDCLP' : yahooSymbol(a))

/** ¿Esta regla la vigila el servidor? Si no (precio manual o sin servidor), la revisa la app al actualizar. */
export const serverWatches = (asset: Asset | undefined) => quotesEnabled() && !!asset && watchSymbol(asset) !== ''

/** Lo que se envía al servidor: reglas activas con símbolo, y la suscripción push si está activada. */
export function buildSyncPayload(alerts: AlertRule[], assets: Asset[], subscription: PushSubscriptionJSON | null) {
  const rules = alerts.flatMap((r) => {
    const a = assets.find((x) => x.id === r.assetId)
    if (r.muted || !a || !serverWatches(a)) return []
    return [
      {
        id: r.id,
        assetId: a.id,
        symbol: watchSymbol(a),
        currency: a.currency,
        kind: r.kind,
        label: describeRule(r, a.currency),
        push: r.channels.includes('push'),
        level: r.level,
        pct: r.pct,
        fast: r.fast,
        slow: r.slow,
        indicator: r.indicator,
        op: r.op,
        threshold: r.threshold,
      },
    ]
  })
  return { rules, subscription }
}

export const putAlerts = (key: string, payload: ReturnType<typeof buildSyncPayload>) => call<{ ok: boolean }>(key, 'PUT', '/alerts', payload)
export const fetchEvents = async (key: string) => (await call<{ events: AlertEvent[] }>(key, 'GET', '/events')).events ?? []
export const sendTestPush = (key: string) => call<{ ok: boolean; status: number }>(key, 'POST', '/test', {})
/** Borra del servidor las reglas, la suscripción y la vinculación de esta instalación. */
export const unlinkServer = (key: string) => call<{ ok: boolean }>(key, 'DELETE', '/alerts')

// ---------- suscripción push del celular ----------

export const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window

function vapidKey() {
  const bin = atob(VAPID_PUBLIC.replace(/-/g, '+').replace(/_/g, '/'))
  const u = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i)
  return u
}

/** Pide permiso y suscribe este dispositivo. Debe llamarse desde un toque del usuario. */
export async function subscribePush(): Promise<PushSubscriptionJSON> {
  if (!pushSupported())
    throw new Error('Este navegador no admite notificaciones push. En iPhone, primero instala la app: Compartir → «Agregar a inicio», y ábrela desde ese ícono.')
  if ((await Notification.requestPermission()) !== 'granted')
    throw new Error('No hay permiso para notificaciones. Actívalo en los ajustes del navegador o del sistema para esta app.')
  const reg = await navigator.serviceWorker.getRegistration()
  if (!reg) throw new Error('Las notificaciones solo funcionan en la app publicada, no en modo de desarrollo.')
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey() })
  return sub.toJSON()
}

/** Suscripción vigente de este dispositivo, o null. */
export async function currentPush(): Promise<PushSubscriptionJSON | null> {
  if (!pushSupported()) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription())?.toJSON() ?? null
}

export async function unsubscribePush() {
  if (!pushSupported()) return
  const reg = await navigator.serviceWorker.getRegistration()
  await (await reg?.pushManager.getSubscription())?.unsubscribe()
}
