import type { AlertRule, AssetType, Channel, Currency } from '../types'

const nf = (min: number, max: number) =>
  new Intl.NumberFormat('es-CL', { minimumFractionDigits: min, maximumFractionDigits: max })
const symbol = (c: Currency) => (c === 'CLP' ? '$' : 'US$')
const signOf = (v: number, withPlus: boolean) => (v < 0 ? '−' : withPlus && v > 0 ? '+' : '')

/** Montos: CLP sin decimales, USD con 2. */
export function fmtMoney(v: number, c: Currency, withPlus = false): string {
  const d = c === 'CLP' ? 0 : 2
  return `${signOf(v, withPlus)}${symbol(c)}${nf(d, d).format(Math.abs(v))}`
}

/** Precios unitarios: en CLP se permiten decimales (acciones bajo $1.000). */
export function fmtPrice(v: number, c: Currency): string {
  const f = c === 'CLP' ? (Math.abs(v) < 1000 ? nf(0, 2) : nf(0, 0)) : nf(2, 2)
  return `${symbol(c)}${f.format(v)}`
}

export const fmtPct = (v: number, withPlus = true) => `${signOf(v, withPlus)}${nf(2, 2).format(Math.abs(v))}%`
export const fmtQty = (v: number) => nf(0, 4).format(v)

export function fmtDate(iso: string, withTime = false): string {
  const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso)
  return new Intl.DateTimeFormat('es-CL', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(d)
}

export const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const typeLabel: Record<AssetType, string> = {
  accion: 'Acciones',
  etf: 'ETF',
  indice: 'Índices',
  divisa: 'Divisas',
  otro: 'Otros',
}

export const channelLabel: Record<Channel, string> = { push: 'Push', correo: 'Correo', telegram: 'Telegram' }

export const kindLabel: Record<AlertRule['kind'], string> = {
  precio_sobre: 'Precio por encima de',
  precio_bajo: 'Precio por debajo de',
  cruce_medias: 'Cruce de medias móviles',
  variacion_dia: 'Variación en un día',
  indicador: 'Indicador técnico',
}

export function describeRule(r: AlertRule, currency: Currency): string {
  switch (r.kind) {
    case 'precio_sobre':
      return `Precio sobre ${fmtPrice(r.level ?? 0, currency)}`
    case 'precio_bajo':
      return `Precio bajo ${fmtPrice(r.level ?? 0, currency)}`
    case 'cruce_medias':
      return `Cruce de medias móviles ${r.fast}/${r.slow} días`
    case 'variacion_dia':
      return `Variación diaria de ±${r.pct}% o más`
    case 'indicador':
      return `${r.indicator} ${r.op === 'menor' ? 'menor que' : 'mayor que'} ${r.threshold}`
  }
}
