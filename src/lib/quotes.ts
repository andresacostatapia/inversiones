import { DOLLAR_URL, QUOTES_URL } from '../config'
import type { Asset, Quote } from '../types'

export const quotesEnabled = () => QUOTES_URL !== ''

/**
 * Símbolo del activo en Yahoo Finance. Si no se indicó uno, se deduce del mercado:
 * la Bolsa de Santiago usa el sufijo ".SN"; NASDAQ y NYSE, el símbolo tal cual.
 * Vacío = este activo no se actualiza solo.
 */
export function yahooSymbol(a: Pick<Asset, 'id' | 'type' | 'exchange' | 'providerSymbol'>): string {
  if (a.providerSymbol !== undefined) return a.providerSymbol
  if (a.type === 'indice' || a.type === 'divisa') return ''
  if (a.exchange === 'Bolsa de Santiago') return `${a.id}.SN`
  if (a.exchange === 'NASDAQ' || a.exchange === 'NYSE') return a.id
  return ''
}

async function getJson(url: string, init?: RequestInit) {
  const res = await fetch(url, { ...init, signal: AbortSignal.timeout(20_000) })
  if (!res.ok) throw new Error(`Respuesta ${res.status}`)
  return res.json()
}

interface RemoteQuote {
  price: number
  prevClose: number
  currency: string
  time: string
}

/** Pide al servidor los precios de los activos que tienen símbolo. Solo viajan los símbolos. */
export async function fetchQuotes(assets: Asset[]): Promise<{ quotes: Quote[]; failed: string[] }> {
  const bySymbol = new Map<string, Asset>()
  for (const a of assets) {
    const s = yahooSymbol(a)
    if (s) bySymbol.set(s, a)
  }
  if (bySymbol.size === 0) return { quotes: [], failed: [] }

  const data: { quotes?: Record<string, RemoteQuote> } = await getJson(`${QUOTES_URL}/quotes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols: [...bySymbol.keys()] }),
  })
  const quotes: Quote[] = []
  const failed: string[] = []
  for (const [symbol, a] of bySymbol) {
    const q = data.quotes?.[symbol]
    // Si la moneda no coincide, el símbolo apunta a otro mercado: mejor no guardar un precio equivocado.
    if (!q || q.currency !== a.currency || !(q.price > 0)) failed.push(a.id)
    else quotes.push({ assetId: a.id, price: q.price, prevClose: q.prevClose > 0 ? q.prevClose : q.price, asOf: q.time, auto: true })
  }
  return { quotes, failed }
}

interface DollarSerie {
  serie: { fecha: string; valor: number }[]
}

/** Dólar observado más reciente y el del día hábil anterior. */
export async function fetchDollar(): Promise<Quote> {
  const { serie }: DollarSerie = await getJson(DOLLAR_URL)
  if (!serie?.length) throw new Error('Sin datos del dólar')
  return { assetId: 'USDCLP', price: serie[0].valor, prevClose: serie[1]?.valor ?? serie[0].valor, asOf: serie[0].fecha, auto: true }
}

// Serie anual del dólar, en memoria: [fecha AAAA-MM-DD, valor] en orden ascendente.
const years = new Map<number, Promise<[string, number][]>>()
function dollarYear(year: number) {
  let p = years.get(year)
  if (!p) {
    p = getJson(`${DOLLAR_URL}/${year}`).then(({ serie }: DollarSerie) =>
      (serie ?? []).map((x): [string, number] => [x.fecha.slice(0, 10), x.valor]).sort((a, b) => a[0].localeCompare(b[0])),
    )
    p.catch(() => years.delete(year))
    years.set(year, p)
  }
  return p
}

/**
 * Dólar observado para cada fecha (AAAA-MM-DD). En fines de semana y feriados no se
 * publica valor: se usa el último publicado antes de esa fecha.
 */
export async function dollarForDates(dates: string[]): Promise<Record<string, number>> {
  const out: Record<string, number> = {}
  for (const date of new Set(dates)) {
    const year = Number(date.slice(0, 4))
    for (const y of [year, year - 1]) {
      const hit = (await dollarYear(y)).findLast(([d]) => d <= date)
      if (hit) {
        out[date] = hit[1]
        break
      }
    }
  }
  return out
}

/** ¿Está operando la Bolsa de Santiago o la de Nueva York? (No considera feriados.) */
export function marketOpen(now = new Date()): boolean {
  const open = (timeZone: string, from: number, to: number) => {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(now)
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
    const minutes = Number(get('hour')) * 60 + Number(get('minute'))
    return !['Sat', 'Sun'].includes(get('weekday')) && minutes >= from && minutes <= to
  }
  // Se deja un margen después del cierre para alcanzar el precio final (Santiago llega con 15 min de retraso).
  return open('America/Santiago', 9 * 60 + 30, 17 * 60 + 30) || open('America/New_York', 9 * 60 + 30, 16 * 60 + 20)
}
