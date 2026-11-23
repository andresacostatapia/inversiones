// Red de la app móvil. En Android, Capacitor reemplaza fetch por HTTP nativo (capacitor.config: CapacitorHttp),
// así Yahoo y mindicador responden sin las restricciones CORS del navegador. En el navegador (desarrollo y
// pruebas) se usa el proxy de Vite: /proxy/yahoo y /proxy/mindicador.
import { Capacitor } from '@capacitor/core'

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

const bases = {
  yahoo: '',
  mindicador: '',
}

export function isNative(): boolean {
  try {
    return Capacitor.isNativePlatform()
  } catch {
    return false
  }
}

export const yahooBase = () => bases.yahoo || (isNative() ? 'https://query1.finance.yahoo.com' : '/proxy/yahoo')
export const mindicadorBase = () => bases.mindicador || (isNative() ? 'https://mindicador.cl' : '/proxy/mindicador')

/** Solo para pruebas: cambia las direcciones base. */
export function setBases(b: Partial<typeof bases>) {
  Object.assign(bases, b)
}

export async function getJson<T = unknown>(url: string, timeoutMs = 15_000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } })
  } catch (e) {
    throw new HttpError(0, ctrl.signal.aborted ? 'tiempo de espera agotado' : `sin conexión (${(e as Error).message})`)
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      /* sin cuerpo JSON */
    }
    const err = new HttpError(res.status, `respuesta ${res.status}`)
    ;(err as HttpError & { body?: unknown }).body = body
    throw err
  }
  return (await res.json()) as T
}
