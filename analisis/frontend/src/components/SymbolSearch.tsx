import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { api } from '../api'
import { CATEGORIES, searchCatalog, toResult, type SearchResult } from '../core/catalog'
import { MARKET_NAMES } from '../core/market'

interface Props {
  watchlist: string[]
  onAdd: (symbol: string) => Promise<void>
}

/** Buscador para agregar a la watchlist: catálogo en español + búsqueda en Yahoo, con aviso de lo que tu perfil
 * no permite comprar (índices, futuros, divisas, apalancados, otras monedas: quedan solo para análisis). */
export function SymbolSearch({ watchlist, onAdd }: Props) {
  const [q, setQ] = useState('')
  const [yahoo, setYahoo] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [explore, setExplore] = useState(false)
  const [active, setActive] = useState(0)
  const seq = useRef(0)
  const wl = useMemo(() => new Set(watchlist.map((s) => s.toUpperCase())), [watchlist])

  const local = useMemo(() => searchCatalog(q, wl), [q, wl])
  // Yahoo, con una pausa breve para no consultar en cada tecla.
  useEffect(() => {
    const query = q.trim()
    setSearchError(null)
    if (query.length < 2) {
      setYahoo([])
      return
    }
    const my = ++seq.current
    const t = window.setTimeout(async () => {
      setSearching(true)
      try {
        const r = await api.search(query)
        if (my === seq.current) setYahoo(r.results.map((x) => ({ ...x, source: 'yahoo' as const })))
      } catch (e) {
        if (my === seq.current) {
          setYahoo([])
          setSearchError(`La búsqueda en Yahoo no respondió (${e instanceof Error ? e.message : e}). `
            + 'Puedes elegir de los sugeridos o escribir el código exacto y apretar Enter.')
        }
      } finally {
        if (my === seq.current) setSearching(false)
      }
    }, 400)
    return () => window.clearTimeout(t)
  }, [q])

  const results = useMemo(() => {
    const seen = new Set(local.map((r) => r.symbol.toUpperCase()))
    return [...local, ...yahoo.filter((r) => !seen.has(r.symbol.toUpperCase()))]
  }, [local, yahoo])

  async function add(symbol: string) {
    setBusy(symbol)
    setError(null)
    try {
      await onAdd(symbol)
      setQ('')
      setExplore(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const exact = results.find((r) => r.symbol.toUpperCase() === q.trim().toUpperCase())
      const pick = exact ?? results[active]
      if (pick) void add(pick.symbol)
      else if (q.trim()) void add(q.trim()) // código exacto que la búsqueda no encontró
    } else if (e.key === 'Escape') {
      setQ('')
    }
  }

  const row = (r: SearchResult, i: number) => (
    <li key={`${r.source}-${r.symbol}`} className={`sr-item${i === active ? ' active' : ''}`}>
      <button type="button" onClick={() => add(r.symbol)} disabled={!!busy || r.in_watchlist} onMouseEnter={() => setActive(i)}>
        <span className="sr-top">
          <b>{r.symbol}</b>
          <span className="sr-name">{r.name}</span>
        </span>
        <span className="sr-meta">
          <span className="tag">{r.kind_label || 'Otro'}</span>
          <span className="tag">{MARKET_NAMES[r.market]}{r.exchange ? ` · ${r.exchange}` : ''}</span>
          {r.in_watchlist && <span className="tag ok">✓ en tu lista</span>}
          {r.note && <span className="tag only" title={r.note}>Solo análisis</span>}
          {busy === r.symbol && <span className="tag">Agregando…</span>}
        </span>
        {r.note && <span className="sr-note">{r.note}</span>}
      </button>
    </li>
  )

  return (
    <div className="symsearch">
      <div className="wl-add">
        <label className="sr-only" htmlFor="add-symbol">Buscar o agregar símbolo</label>
        <input
          id="add-symbol"
          className="input"
          placeholder="Buscar: nvidia, oro, s&p 500, cobre, FALABELLA.SN…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setActive(0)
          }}
          onKeyDown={onKey}
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls="symsearch-list"
        />
        <button type="button" className="btn" onClick={() => setExplore((x) => !x)} aria-expanded={explore}>
          {explore ? 'Cerrar' : 'Populares'}
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {q.trim().length >= 2 && (
        <div className="sr-box">
          {results.length > 0 && <ul id="symsearch-list" className="sr-list" role="listbox">{results.map(row)}</ul>}
          {searching && <p className="muted small sr-status">Buscando en Yahoo…</p>}
          {!searching && !results.length && !searchError && (
            <p className="muted small sr-status">Sin resultados. Si conoces el código exacto, escríbelo y aprieta Enter.</p>
          )}
          {searchError && <p className="muted small sr-status">{searchError}</p>}
        </div>
      )}
      {explore && q.trim().length < 2 && (
        <div className="sr-box">
          {CATEGORIES.map((cat) => (
            <div key={cat.name} className="sr-cat">
              <h3>{cat.name}</h3>
              {cat.hint && <p className="muted small" style={{ margin: '2px 0 6px' }}>{cat.hint}</p>}
              <ul className="sr-list">{cat.items.map((it, i) => row(toResult(it, wl), i + 1000))}</ul>
            </div>
          ))}
        </div>
      )}
      <p className="muted small" style={{ margin: '6px 0 8px' }}>
        «Solo análisis»: se puede seguir y ver su señal, pero tu perfil no permite comprarlo (índices, futuros,
        apalancados u otras monedas).
      </p>
    </div>
  )
}
