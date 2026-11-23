import { useEffect, useState } from 'react'
import { api } from './api'
import { Detail } from './components/Detail'
import { Watchlist } from './components/Watchlist'
import { dateTime, money, time } from './format'
import { useChartColors, usePolling, useThemeSetting, type Theme } from './hooks'

const REFRESH_MS = 30_000
const DISCLAIMER = 'Herramienta educativa, no es asesoría financiera.'

function initialSymbol(): string | null {
  const h = decodeURIComponent(window.location.hash.slice(1))
  if (h) return h.toUpperCase()
  try {
    return localStorage.getItem('symbol')
  } catch {
    return null
  }
}

export function App() {
  const [theme, setTheme] = useThemeSetting()
  const colors = useChartColors()
  const status = usePolling(api.status, REFRESH_MS, [])
  const quotes = usePolling(api.watchlist, REFRESH_MS, [])
  const signals = usePolling(api.signals, REFRESH_MS, [])
  const fx = usePolling(api.fx, REFRESH_MS, [])
  const [selected, setSelected] = useState<string | null>(initialSymbol)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)

  // Si no hay selección (o el símbolo ya no está), elegir el primero de la watchlist.
  useEffect(() => {
    const list = quotes.data
    if (list && list.length && (!selected || !list.some((q) => q.symbol === selected))) setSelected(list[0].symbol)
  }, [quotes.data, selected])

  useEffect(() => {
    if (!selected) return
    if (window.location.hash.slice(1) !== selected) history.replaceState(null, '', `#${selected}`)
    try {
      localStorage.setItem('symbol', selected)
    } catch {
      /* sin almacenamiento */
    }
  }, [selected])

  useEffect(() => {
    const onHash = () => {
      const h = decodeURIComponent(window.location.hash.slice(1)).toUpperCase()
      if (h) setSelected(h)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const reloadLists = () => Promise.all([quotes.reload(), signals.reload()])

  async function refreshNow() {
    setRefreshing(true)
    setRefreshError(null)
    try {
      await api.refresh()
      reloadLists()
      fx.reload()
      status.reload()
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : String(e))
    } finally {
      setRefreshing(false)
    }
  }

  const tz = status.data?.timezone ?? 'America/Santiago'
  const cycleTime = status.data?.last_cycle
  const offline = status.error && !status.data
  const nextTheme: Record<Theme, Theme> = { system: 'dark', dark: 'light', light: 'system' }
  const themeLabel: Record<Theme, string> = { system: 'Tema: sistema', dark: 'Tema: oscuro', light: 'Tema: claro' }
  const quote = quotes.data?.find((q) => q.symbol === selected)

  return (
    <>
      <div className="disclaimer" role="note">⚠ {status.data?.disclaimer ?? DISCLAIMER} La app solo sugiere: no ejecuta órdenes.</div>
      <header className="topbar">
        <h1>Análisis de acciones y ETF</h1>
        <div className="chips" aria-label="Estado de los mercados">
          {status.data?.markets.filter((m) => m.market !== 'FX').map((m) => (
            <span
              className="chip"
              key={m.market}
              title={m.open ? `Cierra ${dateTime(m.closes_at, tz)}` : `Abre ${dateTime(m.next_open, tz)}`}
            >
              <span className={`dot${m.open ? ' on' : ''}`} aria-hidden="true" />
              {m.name}: {m.open ? `abierto hasta ${time(m.closes_at, tz)}` : `cerrado · abre ${dateTime(m.next_open, tz)}`}
            </span>
          ))}
          {fx.data && (
            <span className="chip" title={`${fx.data.source ?? ''} · ${fx.data.freshness.label}`}>
              Dólar {money(fx.data.price, 'CLP')} · {fx.data.freshness.kind === 'diario' ? 'observado' : fx.data.freshness.label.split(' · ')[0].toLowerCase()}
            </span>
          )}
          <span className="chip" title="Último ciclo de actualización del servidor">
            Actualizado {cycleTime ? time(cycleTime, tz) : '—'}
          </span>
        </div>
        <button className="btn" onClick={refreshNow} disabled={refreshing} title="Pedir precios ahora (respeta los límites de cada API)">
          {refreshing ? 'Actualizando…' : '↻ Actualizar'}
        </button>
        <button className="btn icon" onClick={() => setTheme(nextTheme[theme])} title={themeLabel[theme]} aria-label={themeLabel[theme]}>
          {theme === 'dark' ? '☾' : theme === 'light' ? '☀' : '◐'}
        </button>
      </header>
      {offline && (
        <p className="warn" role="alert" style={{ margin: '12px 16px' }}>
          No hay conexión con el servidor. Inícialo con <code>uvicorn app.main:app</code> en analisis/backend.
        </p>
      )}
      {refreshError && <p className="error" role="alert" style={{ margin: '8px 16px' }}>{refreshError}</p>}
      <div className="layout">
        <div className="col">
          <Watchlist
            quotes={quotes.data}
            signals={signals.data?.signals ?? null}
            selected={selected}
            onSelect={setSelected}
            onChanged={reloadLists}
            error={quotes.error && !quotes.data ? quotes.error : null}
          />
        </div>
        {selected ? (
          <Detail key={selected} symbol={selected} quote={quote} colors={colors} refreshMs={REFRESH_MS * 2} />
        ) : (
          <section className="card"><p className="muted">Agrega un símbolo a la watchlist para empezar.</p></section>
        )}
      </div>
      <footer>{DISCLAIMER} Los datos pueden venir con retraso; revisa siempre la hora de cada precio.</footer>
    </>
  )
}
