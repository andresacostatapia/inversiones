/**
 * Servidor de la app Inversiones. Hace dos cosas que el celular no puede hacer solo:
 *
 * 1. Cotizaciones: consulta Yahoo Finance, que no acepta consultas directas desde un navegador.
 * 2. Alertas: cada 5 minutos, en horario de mercado, revisa las reglas guardadas y envía una
 *    notificación push cuando alguna se cumple, aunque la app esté cerrada.
 *
 * Qué guarda (en KV): las reglas de alerta (símbolo y condición), la suscripción push del
 * celular y las últimas alertas cumplidas. NUNCA recibe cantidades, operaciones ni montos.
 *
 *   POST   /quotes   { symbols }                 precios (solo exige venir desde la app)
 *   PUT    /alerts   { rules, subscription }     guarda reglas y suscripción   (requiere clave)
 *   GET    /events                               alertas cumplidas             (requiere clave)
 *   POST   /test                                 notificación de prueba        (requiere clave)
 *   DELETE /alerts                               borra todo y desvincula       (requiere clave)
 *
 * La "clave" es un secreto aleatorio que la app genera y guarda cifrado. El servidor acepta la
 * primera que recibe y desde entonces solo esa (vinculación al primer uso).
 */
import { DOLLAR, SYMBOL, dollar, history, quote } from './market.js'
import { b64url, sendPush, validSubscription } from './push.js'
import { cleanRule, evaluate, formatValue, needsHistory } from './rules.js'

const MAX_SYMBOLS = 40
const MAX_RULES = 100
const MAX_EVENTS = 100
const RULE_SYMBOL = new RegExp(`${SYMBOL.source}|^${DOLLAR}$`)

const read = async (env, key, fallback) => JSON.parse((await env.DB.get(key)) ?? 'null') ?? fallback

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
        headers: { ...cors, 'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE', 'Access-Control-Allow-Headers': 'Content-Type, Authorization', 'Access-Control-Max-Age': '86400' },
      })
    // Solo responde a la app. No es una barrera total (el encabezado se puede falsificar fuera
    // de un navegador), pero evita que otros sitios web usen este servidor.
    if (!allowed) return json({ error: 'Origen no permitido.' }, 403)

    const route = `${request.method} ${new URL(request.url).pathname}`
    let body = {}
    if (request.method === 'POST' || request.method === 'PUT') {
      try {
        body = await request.json()
      } catch {
        return json({ error: 'Cuerpo inválido.' }, 400)
      }
    }

    if (route === 'POST /quotes') {
      const symbols = body.symbols
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
    }

    // De aquí en adelante todo requiere la clave del dispositivo.
    const known = ['PUT /alerts', 'GET /events', 'POST /test', 'DELETE /alerts']
    if (!known.includes(route)) return json({ error: 'No existe.' }, 404)
    if (!env.DB) return json({ error: 'El servidor no tiene almacenamiento configurado.' }, 503)
    const auth = await authorize(request, env, route === 'PUT /alerts')
    if (!auth) return json({ error: 'Este servidor está vinculado a otra instalación de la app.' }, 401)

    if (route === 'PUT /alerts') {
      if (!Array.isArray(body.rules) || body.rules.length > MAX_RULES) return json({ error: 'Lista de reglas inválida.' }, 400)
      const rules = body.rules.map((r) => cleanRule(r, RULE_SYMBOL)).filter(Boolean)
      const sub = body.subscription && validSubscription(body.subscription) ? { endpoint: body.subscription.endpoint, keys: { p256dh: body.subscription.keys.p256dh, auth: body.subscription.keys.auth } } : null
      // Solo se escribe lo que cambió: KV gratuito permite 1.000 escrituras al día.
      await putIfChanged(env, 'rules', rules)
      await putIfChanged(env, 'sub', sub)
      return json({ ok: true, rules: rules.length, push: !!sub })
    }

    if (route === 'GET /events') return json({ events: await read(env, 'events', []) })

    if (route === 'POST /test') {
      const sub = await read(env, 'sub', null)
      if (!sub) return json({ error: 'No hay una suscripción push registrada.' }, 409)
      const status = await sendPush(env, sub, { title: 'Inversiones', body: 'Notificación de prueba: los avisos funcionan.', tag: 'prueba', url: '#/alertas' })
      if (status === 404 || status === 410) await env.DB.delete('sub')
      return json({ ok: status >= 200 && status < 300, status })
    }

    // DELETE /alerts
    await Promise.all(['rules', 'sub', 'state', 'events', 'device'].map((k) => env.DB.delete(k)))
    return json({ ok: true })
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(runAlerts(env))
  },
}

/** Compara la clave recibida con la vinculada. Si aún no hay ninguna y `canLink`, vincula esta. */
async function authorize(request, env, canLink) {
  const m = /^Bearer ([A-Za-z0-9_-]{32,128})$/.exec(request.headers.get('Authorization') ?? '')
  if (!m) return false
  const hash = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(m[1])))
  const stored = await env.DB.get('device')
  if (stored) return stored === hash
  if (!canLink) return false
  await env.DB.put('device', hash)
  return true
}

async function putIfChanged(env, key, value) {
  const next = JSON.stringify(value)
  if ((await env.DB.get(key)) !== next) await env.DB.put(key, next)
}

/**
 * Revisa todas las reglas. Cada regla avisa a lo más una vez por día.
 * Sábados y domingos no hay mercado: no hace nada.
 */
export async function runAlerts(env, now = new Date()) {
  if (!env.DB || now.getUTCDay() === 0 || now.getUTCDay() === 6) return
  const rules = await read(env, 'rules', [])
  if (!rules.length) return

  const today = now.toISOString().slice(0, 10)
  const state = await read(env, 'state', {})
  const fired = state.day === today ? state.fired : {}
  const bySymbol = new Map()
  for (const r of rules) if (!fired[r.id]) bySymbol.set(r.symbol, [...(bySymbol.get(r.symbol) ?? []), r])
  if (!bySymbol.size) return

  const hits = []
  await Promise.all(
    [...bySymbol].map(async ([symbol, list]) => {
      try {
        const m = symbol === DOLLAR ? await dollar() : list.some(needsHistory) ? await history(symbol) : await quote(symbol)
        for (const rule of list) {
          // Si la moneda no coincide, el símbolo apunta a otro mercado: no se evalúa.
          if (m.currency !== rule.currency) continue
          const hit = evaluate(rule, m)
          if (hit) hits.push({ rule, hit })
        }
      } catch {
        // Sin datos para este símbolo en esta pasada: se reintenta en la siguiente.
      }
    }),
  )
  if (!hits.length) return

  const events = await read(env, 'events', [])
  for (const { rule, hit } of hits) {
    fired[rule.id] = true
    hit.message = rule.label + (hit.detail ? ` (${hit.detail})` : '')
    hit.valueText = formatValue(hit, rule.currency)
    events.unshift({ id: crypto.randomUUID(), ruleId: rule.id, assetId: rule.assetId, triggeredAt: now.toISOString(), value: hit.value, valueText: hit.valueText, message: hit.message })
  }
  await env.DB.put('state', JSON.stringify({ day: today, fired }))
  await env.DB.put('events', JSON.stringify(events.slice(0, MAX_EVENTS)))

  const sub = await read(env, 'sub', null)
  if (!sub) return
  for (const { rule, hit } of hits.filter((h) => h.rule.push).slice(0, 5)) {
    const status = await sendPush(env, sub, { title: `${rule.assetId}: ${hit.message}`, body: `Valor actual: ${hit.valueText}`, tag: rule.id, url: '#/alertas' })
    if (status === 404 || status === 410) {
      // El celular canceló la suscripción: se olvida para no seguir intentando.
      await env.DB.delete('sub')
      break
    }
  }
}
