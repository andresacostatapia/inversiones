// Operaciones de series con la semántica de pandas que usa el backend (NaN = sin dato).
// Valores booleanos con posible "sin dato" se representan como 1 / 0 / NaN.

export type Series = number[]

export const nan = (n: number): Series => new Array<number>(n).fill(NaN)
export const isNaNv = (v: number) => Number.isNaN(v)
export const notna = (v: number) => !Number.isNaN(v)

export function shift(s: Series, k: number): Series {
  const out = nan(s.length)
  for (let i = 0; i < s.length; i++) {
    const j = i - k
    if (j >= 0 && j < s.length) out[i] = s[j]
  }
  return out
}

export function map2(a: Series, b: Series, f: (x: number, y: number) => number): Series {
  return a.map((x, i) => f(x, b[i]))
}

/** Media móvil con min_periods = n (si falta algún dato en la ventana, NaN). */
export function rollingMean(s: Series, n: number): Series {
  const out = nan(s.length)
  for (let i = n - 1; i < s.length; i++) {
    let sum = 0
    let ok = true
    for (let j = i - n + 1; j <= i; j++) {
      if (isNaNv(s[j])) {
        ok = false
        break
      }
      sum += s[j]
    }
    if (ok) out[i] = sum / n
  }
  return out
}

/** Desviación estándar móvil poblacional (ddof = 0), como pandas rolling(n).std(ddof=0). */
export function rollingStdPop(s: Series, n: number): Series {
  const out = nan(s.length)
  for (let i = n - 1; i < s.length; i++) {
    let m = 0
    let ok = true
    for (let j = i - n + 1; j <= i; j++) {
      if (isNaNv(s[j])) {
        ok = false
        break
      }
      m += s[j]
    }
    if (!ok) continue
    m /= n
    let v = 0
    for (let j = i - n + 1; j <= i; j++) v += (s[j] - m) ** 2
    out[i] = Math.sqrt(v / n)
  }
  return out
}

/** Mediana móvil que ignora NaN y exige `minPeriods` datos válidos (pandas rolling(n, min_periods).median()). */
export function rollingMedian(s: Series, n: number, minPeriods: number): Series {
  const out = nan(s.length)
  for (let i = 0; i < s.length; i++) {
    const w: number[] = []
    for (let j = Math.max(0, i - n + 1); j <= i; j++) if (notna(s[j])) w.push(s[j])
    if (w.length < minPeriods) continue
    w.sort((a, b) => a - b)
    const k = w.length >> 1
    out[i] = w.length % 2 ? w[k] : (w[k - 1] + w[k]) / 2
  }
  return out
}

export const clip = (v: number, lo: number, hi: number) => (isNaNv(v) ? NaN : Math.min(hi, Math.max(lo, v)))

/** np.interp: interpolación lineal por tramos, constante fuera de los extremos. */
export function interp(x: number, xp: number[], fp: number[]): number {
  if (x <= xp[0]) return fp[0]
  if (x >= xp[xp.length - 1]) return fp[fp.length - 1]
  for (let i = 1; i < xp.length; i++) {
    if (x <= xp[i]) {
      const t = (x - xp[i - 1]) / (xp[i] - xp[i - 1])
      return fp[i - 1] + t * (fp[i] - fp[i - 1])
    }
  }
  return fp[fp.length - 1]
}

/** Velas transcurridas desde el último evento verdadero (0 = hoy, NaN = nunca). */
export function barsSince(event: Series): Series {
  const out = nan(event.length)
  let last = -1
  for (let i = 0; i < event.length; i++) {
    if (event[i] === 1) last = i
    if (last >= 0) out[i] = i - last
  }
  return out
}

/** 1 el día en que una condición (1/0/NaN) cambia, con dato válido el día anterior. */
export function crossed(flag: Series): Series {
  return flag.map((f, i) => {
    if (i === 0) return 0
    const p = flag[i - 1]
    return notna(f) && notna(p) && f !== p ? 1 : 0
  })
}

export function std(values: number[], ddof = 1): number {
  const n = values.length
  if (n - ddof <= 0) return NaN
  const m = values.reduce((a, b) => a + b, 0) / n
  return Math.sqrt(values.reduce((a, b) => a + (b - m) ** 2, 0) / (n - ddof))
}

export const sign = (v: number) => (isNaNv(v) ? NaN : v > 0 ? 1 : v < 0 ? -1 : 0)
