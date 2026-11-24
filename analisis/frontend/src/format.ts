// Formato chileno: punto de miles, coma decimal.

const cache = new Map<string, Intl.NumberFormat>()
function nf(min: number, max: number): Intl.NumberFormat {
  const key = `${min}-${max}`
  let f = cache.get(key)
  if (!f) {
    f = new Intl.NumberFormat('es-CL', { minimumFractionDigits: min, maximumFractionDigits: max })
    cache.set(key, f)
  }
  return f
}

export function num(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return nf(decimals, decimals).format(v)
}

/** Precio: los CLP grandes sin decimales; el resto con 2. */
export function price(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const decimals = currency === 'CLP' && Math.abs(v) >= 1000 ? 0 : 2
  return nf(decimals, decimals).format(v)
}

export function money(v: number | null | undefined, currency?: string | null): string {
  const p = price(v, currency)
  if (p === '—') return p
  return currency === 'USD' ? `US$ ${p}` : currency === 'CLP' ? `$ ${p}` : `${p} ${currency ?? ''}`.trim()
}

export function pct(v: number | null | undefined, decimals = 1, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const s = nf(decimals, decimals).format(v)
  return `${signed && v > 0 ? '+' : ''}${s} %`
}

export function compact(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return new Intl.NumberFormat('es-CL', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

export function time(iso: string | null | undefined, tz = 'America/Santiago'): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', timeZone: tz }).format(new Date(iso))
}

export function dateTime(iso: string | null | undefined, tz = 'America/Santiago'): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('es-CL', {
    weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: tz,
  }).format(new Date(iso))
}

/** '2026-11-23' → '23 nov 2026' */
export function day(d: string | null | undefined): string {
  if (!d) return '—'
  const [y, m, dd] = d.split('-').map(Number)
  return new Intl.DateTimeFormat('es-CL', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(y, m - 1, dd)))
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return ''
  return v > 0 ? 'gain' : 'loss'
}

/** Lee números escritos a mano: "1.234,56", "1,234.56", "262,10", "48.500" (miles chilenos), "1234.5". */
export function parseNum(raw: string): number | null {
  let s = raw.trim().replace(/[^\d.,-]/g, '')
  if (!s || !/\d/.test(s)) return null
  const lastDot = s.lastIndexOf('.')
  const lastComma = s.lastIndexOf(',')
  if (lastDot >= 0 && lastComma >= 0) {
    s = lastComma > lastDot ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '')
  } else if (lastComma >= 0) {
    s = /^-?\d{1,3}(,\d{3})+$/.test(s) ? s.replace(/,/g, '') : s.replace(',', '.')
  } else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) {
    s = s.replace(/\./g, '')
  }
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

export function todayISO(tz = 'America/Santiago'): string {
  return new Date().toLocaleDateString('sv-SE', { timeZone: tz })
}

export function clp(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const s = nf(0, 0).format(Math.abs(v))
  const sign = v < 0 ? '-' : signed && v > 0 ? '+' : ''
  return `${sign}$ ${s}`
}
