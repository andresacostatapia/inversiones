/**
 * Servidor de cotizaciones.
 *
 * La app le envía una lista de símbolos y él consulta Yahoo Finance, que no acepta
 * consultas directas desde un navegador. Solo ve SÍMBOLOS: nunca cantidades,
 * operaciones ni montos. No guarda nada.
 *
 *   POST /quotes   cuerpo: { "symbols": ["AAPL", "SQM-B.SN"] }
 *   respuesta:     { "quotes": { "AAPL": { price, prevClose, currency, time } },
 *                    "errors": { "XXXX": "motivo" } }
 */

const MAX_SYMBOLS = 40
const SYMBOL = /^[A-Z0-9.\-=^]{1,20}$/
const TTL_MS = 60_000
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'

// Memoria breve para no repetir consultas a Yahoo dentro del mismo minuto.
const memo = new Map()

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') ?? ''
    const allowed = (env.ALLOWED_ORIGINS ?? '').split(',').includes(origin)
    const cors = allowed ? { 'Access-Control-Allow-Origin': origin, Vary: 'Origin' } : {}
    const json = (body, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...cors } })

    if (request.method === 'OPTIONS')
      return new Response(null, {
        status: 204,
        headers: { ...cors, 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400' },
      })
    if (new URL(request.url).pathname !== '/quotes' || request.method !== 'POST') return json({ error: 'No existe.' }, 404)
    // Solo responde a la app. No es una barrera total (el encabezado se puede falsificar
    // fuera de un navegador), pero evita que otros sitios web usen este servidor.
    if (!allowed) return json({ error: 'Origen no permitido.' }, 403)

    let symbols
    try {
      symbols = (await request.json()).symbols
    } catch {
      return json({ error: 'Cuerpo inválido.' }, 400)
    }
    if (!Array.isArray(symbols) || symbols.length === 0 || symbols.length > MAX_SYMBOLS || !symbols.every((s) => typeof s === 'string' && SYMBOL.test(s)))
      return json({ error: `Se espera una lista de 1 a ${MAX_SYMBOLS} símbolos válidos.` }, 400)

    const quotes = {}
    const errors = {}
    await Promise.all(
      [...new Set(symbols)].map(async (symbol) => {
        try {
          quotes[symbol] = await quote(symbol)
        } catch (err) {
          errors[symbol] = err.message
        }
      }),
    )
    return json({ quotes, errors })
  },
}

async function quote(symbol) {
  const hit = memo.get(symbol)
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value

  const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1d`, {
    headers: { 'User-Agent': UA, Accept: 'application/json' },
  })
  if (res.status === 404) throw new Error('Símbolo no encontrado.')
  if (res.status === 429) throw new Error('El proveedor está limitando las consultas. Intenta más tarde.')
  if (!res.ok) throw new Error(`El proveedor respondió ${res.status}.`)

  const meta = (await res.json())?.chart?.result?.[0]?.meta
  if (typeof meta?.regularMarketPrice !== 'number') throw new Error('Sin precio disponible.')
  const value = {
    price: meta.regularMarketPrice,
    prevClose: typeof meta.chartPreviousClose === 'number' ? meta.chartPreviousClose : meta.regularMarketPrice,
    currency: meta.currency ?? '',
    time: meta.regularMarketTime ? new Date(meta.regularMarketTime * 1000).toISOString() : new Date().toISOString(),
  }
  if (memo.size > 500) memo.clear()
  memo.set(symbol, { at: Date.now(), value })
  return value
}
