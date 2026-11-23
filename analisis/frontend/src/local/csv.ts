// Lectura del CSV de la PWA (Historial → Exportar CSV): misma lógica que portfolio_service.py.
import { parseNum } from '../format'

const norm = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().trim().replace(/\s+/g, ' ')

const HEADERS: Record<string, string[]> = {
  date: ['fecha', 'date'],
  symbol: ['activo', 'simbolo', 'symbol', 'ticker', 'instrumento'],
  side: ['tipo', 'side', 'operacion', 'tipo de operacion'],
  quantity: ['cantidad', 'quantity', 'unidades', 'acciones'],
  price: ['precio', 'price', 'precio unitario'],
  fee: ['comision', 'fee', 'comisiones'],
  tax: ['impuestos', 'tax', 'impuesto'],
  currency: ['moneda', 'currency'],
  fx: ['tipo de cambio', 'fx', 'fx rate', 'dolar'],
  note: ['nota', 'note', 'notas', 'comentario'],
}

export function mapHeaders(header: string[]): Record<string, number> {
  const out: Record<string, number> = {}
  header.forEach((h, i) => {
    const n = norm(h)
    for (const [key, names] of Object.entries(HEADERS)) if (names.includes(n) && !(key in out)) out[key] = i
  })
  return out
}

export function parseCsv(text: string): string[][] {
  const clean = text.replace(/^﻿/, '')
  const first = clean.split(/\r?\n/)[0] ?? ''
  const delim = [';', '\t', ','].sort((a, b) => first.split(b).length - first.split(a).length)[0]
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < clean.length; i++) {
    const c = clean[i]
    if (quoted) {
      if (c === '"' && clean[i + 1] === '"') {
        cell += '"'
        i++
      } else if (c === '"') quoted = false
      else cell += c
    } else if (c === '"') quoted = true
    else if (c === delim) {
      row.push(cell)
      cell = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && clean[i + 1] === '\n') i++
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else cell += c
  }
  if (cell !== '' || row.length) {
    row.push(cell)
    rows.push(row)
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ''))
}

/** AAAA-MM-DD, DD/MM/AAAA o DD-MM-AAAA → AAAA-MM-DD (o null). */
export function parseDate(raw: string): string | null {
  const s = (raw ?? '').trim().slice(0, 10)
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (m) return s
  m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(s)
  if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`
  return null
}

export function parseSide(raw: string): 'compra' | 'venta' | null {
  const n = norm(raw ?? '')
  if (['compra', 'buy', 'c', 'b', 'comprar'].includes(n)) return 'compra'
  if (['venta', 'sell', 'v', 's', 'vender'].includes(n)) return 'venta'
  return null
}

export { parseNum }

/** "%g" de Python para cantidades y precios en la huella de duplicados. */
export const fmtG = (v: number) => String(Number(v.toPrecision(6)))

export const fingerprint = (date: string, symbol: string, side: string, qty: number, price: number) =>
  `${date}|${symbol}|${side}|${fmtG(qty)}|${fmtG(price)}`
