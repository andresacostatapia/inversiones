// Mercados: horario con feriados (tablas exportadas de exchange_calendars) y etiqueta de frescura del dato.
// Misma lógica que backend/app/market_hours.py.

import calendars from './calendars.json'

export type Market = 'CL' | 'US' | 'FX'
export const MARKET_NAMES: Record<Market, string> = { CL: 'Bolsa de Santiago', US: 'EE.UU.', FX: 'Dólar (USD/CLP)' }

export function marketOf(symbol: string): Market {
  const s = symbol.toUpperCase()
  if (s.endsWith('.SN')) return 'CL'
  if (s.endsWith('=X') || s === 'USDCLP') return 'FX'
  return 'US'
}

interface Encoded {
  start: number
  deltas: number[]
  times: [number, number, number][]
}

interface Calendar {
  days: number[] // día de cada sesión (días desde 1970-01-01)
  open: number[] // minuto UTC de apertura
  close: number[] // minuto UTC de cierre
  byDay: Map<number, number>
}

function decode(e: Encoded): Calendar {
  const days = [e.start]
  for (const d of e.deltas) days.push(days[days.length - 1] + d)
  const open: number[] = []
  const close: number[] = []
  for (const [count, o, c] of e.times) {
    for (let i = 0; i < count; i++) {
      open.push(o)
      close.push(c)
    }
  }
  return { days, open, close, byDay: new Map(days.map((d, i) => [d, i])) }
}

const CALS: Record<'CL' | 'US', Calendar> = {
  CL: decode(calendars.XSGO as unknown as Encoded),
  US: decode(calendars.XNYS as unknown as Encoded),
}
const calFor = (m: Market) => CALS[m === 'FX' ? 'CL' : m]

const DAY = 864e5
const MIN = 6e4

/** ¿Está abierto en ese instante? El dólar sigue el horario de Santiago (fuera de él casi no se mueve). */
export function isOpen(market: Market, now: Date = new Date()): boolean {
  const cal = calFor(market)
  const t = Math.floor(now.getTime() / MIN) * MIN
  const day = Math.floor(t / DAY)
  const i = cal.byDay.get(day)
  if (i === undefined) return false
  const minute = Math.round((t - day * DAY) / MIN)
  return minute >= cal.open[i] && minute < cal.close[i]
}

const at = (day: number, minute: number) => new Date(day * DAY + minute * MIN).toISOString()

export function sessionInfo(market: Market, now: Date = new Date()) {
  const open = isOpen(market, now)
  const info: { market: Market; name: string; open: boolean; closes_at?: string; next_open?: string } = {
    market, name: MARKET_NAMES[market], open,
  }
  const cal = calFor(market)
  const t = now.getTime()
  if (open) {
    const day = Math.floor(t / DAY)
    const i = cal.byDay.get(day)!
    info.closes_at = at(day, cal.close[i])
  } else {
    for (let i = 0; i < cal.days.length; i++) {
      const o = cal.days[i] * DAY + cal.open[i] * MIN
      if (o > t) {
        info.next_open = at(cal.days[i], cal.open[i])
        break
      }
    }
  }
  return info
}

// --- Frescura del dato ---------------------------------------------------------------

export type FreshnessKind = 'tiempo_real' | 'retraso' | 'cierre' | 'desactualizado' | 'diario' | 'sin_datos'

export interface Freshness {
  kind: FreshnessKind
  label: string
  as_of_local: string | null
  age_minutes: number | null
}

function localParts(d: Date, tz: string) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hourCycle: 'h23',
  }).formatToParts(d).map((x) => [x.type, x.value]))
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second)
  const offMin = Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / MIN)
  return { ...p, offMin } as Record<string, string> & { offMin: number }
}

function isoLocal(d: Date, tz: string): string {
  const p = localParts(d, tz)
  const sign = p.offMin >= 0 ? '+' : '-'
  const a = Math.abs(p.offMin)
  const off = `${sign}${String(Math.floor(a / 60)).padStart(2, '0')}:${String(a % 60).padStart(2, '0')}`
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}${off}`
}

function ageText(m: number): string {
  if (m < 1) return 'menos de 1 min'
  if (m < 90) return `${Math.floor(m)} min`
  const h = Math.floor((m * 60) / 3600)
  return h < 48 ? `${h} h` : `${Math.floor(h / 24)} días`
}

export function freshness(asOf: Date | null, opts: { realtime: boolean; marketOpen: boolean; now?: Date;
  staleAfterMinutes?: number; tz?: string; daily?: boolean }): Freshness {
  const now = opts.now ?? new Date()
  const tz = opts.tz ?? 'America/Santiago'
  if (!asOf) return { kind: 'sin_datos', label: 'Sin datos', as_of_local: null, age_minutes: null }
  const p = localParts(asOf, tz)
  const pn = localParts(now, tz)
  const age = Math.max(0, (now.getTime() - asOf.getTime()) / MIN)
  const hhmm = `${p.hour}:${p.minute}`
  const sameDay = p.year === pn.year && p.month === pn.month && p.day === pn.day
  const when = sameDay ? hhmm : `${p.day}/${p.month} ${hhmm}`
  let kind: FreshnessKind
  let label: string
  if (opts.daily) {
    kind = 'diario'
    label = `Dato diario oficial del ${p.day}/${p.month}`
  } else if (!opts.marketOpen) {
    kind = 'cierre'
    label = `Mercado cerrado · último precio ${when}`
  } else if (age > (opts.staleAfterMinutes ?? 30)) {
    kind = 'desactualizado'
    label = `Desactualizado · dato de ${when} (hace ${ageText(age)})`
  } else if (opts.realtime && age <= 2) {
    kind = 'tiempo_real'
    label = `Tiempo real · ${when}`
  } else {
    kind = 'retraso'
    label = `Con retraso · dato de ${when} (hace ${ageText(age)})`
  }
  return { kind, label, as_of_local: isoLocal(asOf, tz), age_minutes: Math.round(age * 10) / 10 }
}
