import type { NewTrade } from '../types'

/** Huella de una operación: dos filas con la misma huella se consideran duplicadas. */
export const fingerprint = (t: Pick<NewTrade, 'date' | 'assetId' | 'side' | 'quantity' | 'price' | 'brokerId'>) =>
  [t.date, t.assetId, t.side, t.quantity, t.price, t.brokerId].join('|')

export function hashText(text: string): string {
  let h = 5381
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0
  return h.toString(16)
}

export function toCsv(rows: (string | number)[][]): string {
  const esc = (v: string | number) => {
    const s = String(v)
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return rows.map((r) => r.map(esc).join(';')).join('\r\n')
}

export function download(fileName: string, text: string, mime = 'text/csv') {
  // En CSV se agrega BOM para que Excel abra bien los acentos
  const body = mime === 'text/csv' ? '﻿' + text : text
  const url = URL.createObjectURL(new Blob([body], { type: `${mime};charset=utf-8` }))
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  a.click()
  URL.revokeObjectURL(url)
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
      if (c === '"' && clean[i + 1] === '"') (cell += '"'), i++
      else if (c === '"') quoted = false
      else cell += c
    } else if (c === '"') quoted = true
    else if (c === delim) (row.push(cell), (cell = ''))
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && clean[i + 1] === '\n') i++
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else cell += c
  }
  if (cell !== '' || row.length) (row.push(cell), rows.push(row))
  return rows.filter((r) => r.some((c) => c.trim() !== ''))
}

/** Acepta "1.234,56", "1,234.56", "262,10" y "6.850" (miles en formato chileno). */
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

/** Acepta AAAA-MM-DD, DD/MM/AAAA y DD-MM-AAAA. Devuelve AAAA-MM-DD o ''. */
export function parseDate(raw: string): string {
  const s = raw.trim().slice(0, 10)
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (m) return s
  m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(s)
  if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`
  return ''
}
