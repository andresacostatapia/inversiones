/** Evaluación de reglas de alerta. Funciones puras: reciben datos de mercado y dicen si la regla se cumple. */

const sma = (v, n, end) => {
  let s = 0
  for (let i = end - n; i < end; i++) s += v[i]
  return s / n
}

/** RSI de Wilder sobre los primeros `end` cierres. */
function rsi(v, period, end) {
  if (end < period + 1) return null
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const d = v[i] - v[i - 1]
    if (d >= 0) gain += d
    else loss -= d
  }
  gain /= period
  loss /= period
  for (let i = period + 1; i < end; i++) {
    const d = v[i] - v[i - 1]
    gain = (gain * (period - 1) + Math.max(d, 0)) / period
    loss = (loss * (period - 1) + Math.max(-d, 0)) / period
  }
  return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss)
}

function ema(v, n, end) {
  if (end < n) return null
  const k = 2 / (n + 1)
  let e = sma(v, n, n)
  for (let i = n; i < end; i++) e = v[i] * k + e * (1 - k)
  return e
}
/** Línea MACD (EMA 12 − EMA 26) sobre los primeros `end` cierres. */
function macd(v, end) {
  const a = ema(v, 12, end)
  const b = ema(v, 26, end)
  return a === null || b === null ? null : a - b
}

const crosses = (before, now, op, threshold) =>
  op === 'menor' ? before >= threshold && now < threshold : before <= threshold && now > threshold

/**
 * ¿Se cumple la regla con estos datos? Devuelve null si no, o { value, kind, detail }.
 * - Precio y variación diaria: se cumplen mientras la condición sea cierta.
 * - Cruce de medias e indicadores: solo el día en que CAMBIAN (ayer no, hoy sí).
 */
export function evaluate(rule, m) {
  const c = m.closes
  switch (rule.kind) {
    case 'precio_sobre':
      return m.price > rule.level ? { value: m.price, kind: 'price' } : null
    case 'precio_bajo':
      return m.price < rule.level ? { value: m.price, kind: 'price' } : null
    case 'variacion_dia': {
      const pct = m.prevClose ? (m.price / m.prevClose - 1) * 100 : 0
      return Math.abs(pct) >= rule.pct ? { value: pct, kind: 'pct' } : null
    }
    case 'cruce_medias': {
      const n = c.length
      if (!(rule.fast > 0 && rule.slow > rule.fast) || n < rule.slow + 1) return null
      const f0 = sma(c, rule.fast, n)
      const s0 = sma(c, rule.slow, n)
      const f1 = sma(c, rule.fast, n - 1)
      const s1 = sma(c, rule.slow, n - 1)
      if (f1 <= s1 && f0 > s0) return { value: m.price, kind: 'price', detail: 'al alza' }
      if (f1 >= s1 && f0 < s0) return { value: m.price, kind: 'price', detail: 'a la baja' }
      return null
    }
    case 'indicador': {
      const n = c.length
      const f = rule.indicator === 'MACD' ? (end) => macd(c, end) : (end) => rsi(c, 14, end)
      const now = f(n)
      const before = f(n - 1)
      if (now === null || before === null) return null
      return crosses(before, now, rule.op, rule.threshold) ? { value: now, kind: 'number' } : null
    }
    default:
      return null
  }
}

export const needsHistory = (rule) => rule.kind === 'cruce_medias' || rule.kind === 'indicador'

/** Formato chileno: miles con punto, decimales con coma. */
function num(v, decimals) {
  const [int, dec] = Math.abs(v).toFixed(decimals).split('.')
  return (v < 0 ? '−' : '') + int.replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (dec ? ',' + dec : '')
}
export function formatValue(hit, currency) {
  if (hit.kind === 'pct') return `${hit.value > 0 ? '+' : ''}${num(hit.value, 2)}%`
  if (hit.kind === 'number') return num(hit.value, 2)
  return currency === 'USD' ? `US$${num(hit.value, 2)}` : `$${num(hit.value, Math.abs(hit.value) < 1000 ? 2 : 0)}`
}

/** Valida y limpia una regla recibida de la app. Devuelve null si no es utilizable. */
export function cleanRule(r, symbolPattern) {
  const n = (x) => (typeof x === 'number' && Number.isFinite(x) ? x : undefined)
  const s = (x, max) => (typeof x === 'string' ? x.slice(0, max) : '')
  const kinds = ['precio_sobre', 'precio_bajo', 'cruce_medias', 'variacion_dia', 'indicador']
  if (!r || !kinds.includes(r.kind) || !s(r.id, 40) || !symbolPattern.test(r.symbol ?? '')) return null
  const rule = {
    id: s(r.id, 40),
    assetId: s(r.assetId, 20),
    symbol: r.symbol,
    currency: r.currency === 'USD' ? 'USD' : 'CLP',
    kind: r.kind,
    label: s(r.label, 120),
    push: r.push === true,
    level: n(r.level),
    pct: n(r.pct),
    fast: n(r.fast),
    slow: n(r.slow),
    indicator: r.indicator === 'MACD' ? 'MACD' : 'RSI',
    op: r.op === 'mayor' ? 'mayor' : 'menor',
    threshold: n(r.threshold),
  }
  const complete =
    ((rule.kind === 'precio_sobre' || rule.kind === 'precio_bajo') && rule.level !== undefined) ||
    (rule.kind === 'variacion_dia' && rule.pct !== undefined) ||
    (rule.kind === 'cruce_medias' && rule.fast !== undefined && rule.slow !== undefined) ||
    (rule.kind === 'indicador' && rule.threshold !== undefined)
  return complete ? rule : null
}
