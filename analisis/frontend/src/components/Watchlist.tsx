import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { api } from '../api'
import { price } from '../format'
import type { QuoteView, Signal } from '../types'
import { Change, FreshnessBadge, SignalBadge } from './Badges'

interface Props {
  quotes: QuoteView[] | null
  signals: Signal[] | null
  selected: string | null
  onSelect: (symbol: string) => void
  onChanged: () => Promise<unknown>
  error: string | null
}

const MARKET = { CL: 'Chile', US: 'Internacional', FX: 'Divisas' } as const

export function Watchlist({ quotes, signals, selected, onSelect, onChanged, error }: Props) {
  const [symbol, setSymbol] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const bySymbol = new Map((signals ?? []).map((s) => [s.symbol, s]))

  async function add(e: FormEvent) {
    e.preventDefault()
    const s = symbol.trim()
    if (!s) return
    setBusy(true)
    setMsg(null)
    try {
      const q = await api.addSymbol(s)
      setSymbol('')
      await onChanged() // recargar antes de seleccionar: si no, el símbolo aún no está en la lista
      onSelect(q.symbol)
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(sym: string) {
    if (!window.confirm(`¿Quitar ${sym} de la watchlist?`)) return
    try {
      await api.removeSymbol(sym)
      await onChanged()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    }
  }

  function onKey(e: KeyboardEvent<HTMLTableRowElement>, sym: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(sym)
    }
  }

  const groups = (['CL', 'US'] as const).map((m) => ({ m, rows: (quotes ?? []).filter((q) => q.market === m) }))

  return (
    <section className="card" aria-labelledby="wl-title">
      <div className="card-head">
        <h2 id="wl-title">Watchlist</h2>
        <span className="muted small">{quotes ? `${quotes.length} símbolos` : ''}</span>
      </div>
      <form className="wl-add" onSubmit={add}>
        <label className="sr-only" htmlFor="add-symbol">Agregar símbolo</label>
        <input
          id="add-symbol"
          className="input"
          placeholder="Agregar: FALABELLA.SN, QQQ…"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          disabled={busy}
          autoComplete="off"
          spellCheck={false}
        />
        <button className="btn primary" disabled={busy || !symbol.trim()}>{busy ? 'Validando…' : 'Agregar'}</button>
      </form>
      <p className="muted small" style={{ margin: '0 0 8px' }}>Chile usa el sufijo .SN (p. ej. SQM-B.SN).</p>
      {msg && <p className="error" role="alert">{msg}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!quotes && !error && <p className="muted">Cargando…</p>}
      {quotes && (
        <table className="wl">
          <thead>
            <tr>
              <th>Símbolo</th>
              <th className="r">Precio</th>
              <th className="r">Señal</th>
              <th aria-label="Quitar" />
            </tr>
          </thead>
          {groups.filter((g) => g.rows.length).map((g) => (
            <tbody key={g.m}>
              <tr>
                <td colSpan={4} className="muted small" style={{ paddingTop: 10, fontWeight: 600 }}>{MARKET[g.m]}</td>
              </tr>
              {g.rows.map((q) => {
                const s = bySymbol.get(q.symbol)
                return (
                  <tr
                    key={q.symbol}
                    className="row"
                    tabIndex={0}
                    aria-selected={selected === q.symbol}
                    onClick={() => onSelect(q.symbol)}
                    onKeyDown={(e) => onKey(e, q.symbol)}
                  >
                    <td>
                      <div className="sym">{q.symbol}</div>
                      <div className="name" title={q.name ?? ''}>{q.name ?? '—'}</div>
                      <FreshnessBadge f={q.freshness} short />
                    </td>
                    <td className="r">
                      <div className="num">{price(q.price, q.currency)} <span className="muted small">{q.currency}</span></div>
                      <div className="small"><Change v={q.change_pct} /></div>
                      {q.status === 'error' && <div className="error small" title={q.error ?? ''}>⚠ error al actualizar</div>}
                    </td>
                    <td className="r">
                      <SignalBadge label={s?.label} />
                      {s?.score != null && (
                        <div className="small muted num">
                          {s.score > 0 ? '+' : ''}{s.score.toFixed(0)} · {s.confidence?.toFixed(0)} %
                        </div>
                      )}
                      {s?.changed && <div className="small" style={{ color: 'var(--accent)' }}>cambió</div>}
                    </td>
                    <td>
                      <button
                        className="remove"
                        title={`Quitar ${q.symbol}`}
                        aria-label={`Quitar ${q.symbol}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          remove(q.symbol)
                        }}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          ))}
        </table>
      )}
      <p className="muted small" style={{ margin: '10px 0 0' }}>
        Señal: puntaje (−100 a +100) · confianza.
      </p>
    </section>
  )
}
