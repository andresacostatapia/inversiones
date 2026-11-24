// Formato de números en español, idéntico al de backend/app/textfmt.py (las razones de la señal deben coincidir).

/** toFixed con desempate al par, como Python ("{:.Nf}" y round()).
 * toFixed ya redondea el valor binario EXACTO; solo difiere de Python en empates exactos (2.5 → "2" en Python, "3" en JS).
 * El empate se detecta en la expansión decimal exacta: multiplicar por 10^nd puede inventar un empate (5.525 × 100 = 552.5). */
export function toFixedPy(v: number, nd: number): string {
  const a = Math.abs(v)
  const neg = v < 0 || Object.is(v, -0) // como Python: un negativo que redondea a cero conserva el signo ("-0.0")
  let s = a.toFixed(nd)
  if (a < 1e21) {
    const full = a.toFixed(Math.min(100, nd + 60)) // expansión exacta para las magnitudes que usamos
    const dot = full.indexOf('.')
    const tail = full.slice(dot + 1 + nd)
    if (/^50*$/.test(tail)) {
      const truncated = nd ? full.slice(0, dot + 1 + nd) : full.slice(0, dot)
      const lastDigit = Number(truncated[truncated.length - 1])
      if (lastDigit % 2 === 0) s = truncated // al par: se trunca; si es impar, toFixed ya subió
    }
  }
  return neg ? `-${s}` : s
}

/** round(x, nd) de Python. */
export function pyRound(v: number, nd = 0): number {
  return Number(toFixedPy(v, nd))
}

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/** num(v, nd, suffix) de textfmt.py: punto de miles, coma decimal; "—" si no hay número. */
export function num(v: number | null | undefined, nd = 2, suffix = ''): string {
  if (!isNum(v)) return '—'
  const s = toFixedPy(v, nd)
  const neg = s.startsWith('-')
  const [int, dec] = (neg ? s.slice(1) : s).split('.')
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, '.')
  return `${neg ? '-' : ''}${grouped}${dec !== undefined ? `,${dec}` : ''}${suffix}`
}

export function pct(v: number | null | undefined, nd = 1, signed = true): string {
  if (!isNum(v)) return '—'
  return (signed && v > 0 ? '+' : '') + num(v, nd, ' %')
}

export function days(n: number): string {
  const k = Math.trunc(n)
  return k === 0 ? 'hoy' : k === 1 ? 'hace 1 día' : `hace ${k} días`
}
