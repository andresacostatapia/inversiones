// Catálogo de instrumentos populares (con nombres y sinónimos en español) y búsqueda local en él.
import data from './catalog.json'
import { marketOf, type Market } from './market'
import { investability } from './portfolio'

export const KIND_LABELS: Record<string, string> = {
  EQUITY: 'Acción', ETF: 'ETF', INDEX: 'Índice', FUTURE: 'Futuro', CURRENCY: 'Divisa',
  CRYPTOCURRENCY: 'Cripto', MUTUALFUND: 'Fondo', OPTION: 'Opción',
}

export interface SearchResult {
  symbol: string
  name: string
  exchange: string | null
  kind: string | null
  kind_label: string
  market: Market
  note: string | null // por qué el perfil no permite comprarlo (null = se puede)
  in_watchlist: boolean
  source: 'catalogo' | 'yahoo'
}

interface Item {
  symbol: string
  name: string
  kind: string
  aka?: string
  currency?: string
}

export const CATEGORIES = (data.categories as { name: string; hint?: string; items: Item[] }[])

const norm = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim()

export function toResult(it: Item, watchlist: Set<string>): SearchResult {
  return {
    symbol: it.symbol, name: it.name, exchange: null, kind: it.kind, kind_label: KIND_LABELS[it.kind] ?? it.kind,
    market: marketOf(it.symbol), note: investability(it.symbol, it.kind, it.currency ?? null, it.name),
    in_watchlist: watchlist.has(it.symbol.toUpperCase()), source: 'catalogo',
  }
}

/** Coincidencias del catálogo: por código, nombre o sinónimos ("oro" → GLD, IAU, GC=F). */
export function searchCatalog(query: string, watchlist: Set<string>, limit = 8): SearchResult[] {
  const q = norm(query)
  if (q.length < 2) return []
  const words = q.split(' ')
  const scored: [number, Item][] = []
  for (const cat of CATEGORIES) {
    for (const it of cat.items) {
      const sym = it.symbol.toLowerCase()
      // Cada palabra buscada debe ser el comienzo de una palabra del código, nombre o sinónimos
      // ("oro" encuentra "oro", no "Concha y Toro").
      const hay = norm(`${it.symbol} ${it.name} ${it.aka ?? ''}`).split(/[\s()]+/)
      if (!words.every((w) => hay.some((h) => h.startsWith(w)))) continue
      const score = sym === q ? 0 : sym.startsWith(q) ? 1 : norm(it.name).startsWith(q) ? 2 : 3
      scored.push([score, it])
    }
  }
  scored.sort((a, b) => a[0] - b[0])
  return scored.slice(0, limit).map(([, it]) => toResult(it, watchlist))
}
