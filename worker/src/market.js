/** Datos de mercado: Yahoo Finance para acciones y ETF; mindicador.cl para el dólar observado. */

const TTL_MS = 60_000
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
export const SYMBOL = /^[A-Z0-9.\-=^]{1,20}$/
export const DOLLAR = 'USDCLP'

// Memoria breve para no repetir consultas dentro del mismo minuto.
const memo = new Map()

async function chart(symbol, range) {
  const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=${range}&interval=1d`, {
    headers: { 'User-Agent': UA, Accept: 'application/json' },
  })
  if (res.status === 404) throw new Error('Símbolo no encontrado.')
  if (res.status === 429) throw new Error('El proveedor está limitando las consultas. Intenta más tarde.')
  if (!res.ok) throw new Error(`El proveedor respondió ${res.status}.`)
  const result = (await res.json())?.chart?.result?.[0]
  const meta = result?.meta
  if (typeof meta?.regularMarketPrice !== 'number') throw new Error('Sin precio disponible.')
  return {
    price: meta.regularMarketPrice,
    prevClose: typeof meta.chartPreviousClose === 'number' ? meta.chartPreviousClose : meta.regularMarketPrice,
    currency: meta.currency ?? '',
    time: meta.regularMarketTime ? new Date(meta.regularMarketTime * 1000).toISOString() : new Date().toISOString(),
    closes: (result.indicators?.quote?.[0]?.close ?? []).filter((v) => typeof v === 'number'),
  }
}

/** Último precio y cierre anterior de un símbolo. */
export async function quote(symbol) {
  const hit = memo.get(symbol)
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value
  const { closes: _closes, ...value } = await chart(symbol, '1d')
  if (memo.size > 500) memo.clear()
  memo.set(symbol, { at: Date.now(), value })
  return value
}

/**
 * Precio actual más cierres diarios de dos años (para medias móviles e indicadores).
 * Con rango largo, Yahoo informa como "cierre anterior" el previo al rango, así que
 * el cierre de ayer se toma de la propia serie.
 */
export async function history(symbol) {
  const data = await chart(symbol, '2y')
  const closes = data.closes
  if (closes.length >= 2) data.prevClose = closes[closes.length - 2]
  if (closes.length) closes[closes.length - 1] = data.price
  return data
}

/** Dólar observado: el más reciente y el del día hábil anterior. */
export async function dollar() {
  const res = await fetch('https://mindicador.cl/api/dolar', { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`mindicador respondió ${res.status}.`)
  const { serie } = await res.json()
  if (!serie?.length) throw new Error('Sin datos del dólar.')
  return { price: serie[0].valor, prevClose: serie[1]?.valor ?? serie[0].valor, currency: 'CLP', time: serie[0].fecha, closes: [] }
}
