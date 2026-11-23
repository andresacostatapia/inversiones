import { useCallback, useEffect, useState } from 'react'
import { api, LOCAL_MODE } from './api'
import { AlertsView } from './components/AlertsView'
import { ContributionCard } from './components/ContributionCard'
import { Detail } from './components/Detail'
import { PortfolioView } from './components/PortfolioView'
import { TransactionsCard } from './components/TransactionsCard'
import { Watchlist } from './components/Watchlist'
import { dateTime, money, time } from './format'
import { useChartColors, usePolling, useThemeSetting, type Theme } from './hooks'

const REFRESH_MS = 30_000
const DISCLAIMER = 'Herramienta educativa, no es asesoría financiera.'

type View = 'mercado' | 'cartera' | 'alertas'

// Rutas: #/cartera, #/alertas, o #SIMBOLO (mercado con ese símbolo seleccionado).
function parseHash(): { view: View; symbol: string | null } {
  const h = decodeURIComponent(window.location.hash.slice(1))
  if (h === '/cartera') return { view: 'cartera', symbol: null }
  if (h === '/alertas') return { view: 'alertas', symbol: null }
  return { view: 'mercado', symbol: h ? h.toUpperCase() : null }
}

function storedSymbol(): string | null {
  try {
    return localStorage.getItem('symbol')
  } catch {
    return null
  }
}

function PortfolioPage({ onSelect }: { onSelect: (s: string) => void }) {
  const pf = usePolling(api.portfolio, REFRESH_MS * 2, [])
  const txs = usePolling(api.transactions, 0, [])
  const reload = () => {
    pf.reload()
    txs.reload()
  }
  return (
    <div className="col page">
      {pf.error && !pf.data && <section className="card"><p className="error">{pf.error}</p></section>}
      {pf.data && <PortfolioView data={pf.data} onSelect={onSelect} />}
      {pf.data && <ContributionCard defaultAmount={pf.data.monthly_contribution_clp} />}
      <TransactionsCard txs={txs.data} onChanged={reload} />
    </div>
  )
}

export function App() {
  const [theme, setTheme] = useThemeSetting()
  const colors = useChartColors()
  const status = usePolling(api.status, REFRESH_MS, [])
  const quotes = usePolling(api.watchlist, REFRESH_MS, [])
  const signals = usePolling(api.signals, REFRESH_MS, [])
  const fx = usePolling(api.fx, REFRESH_MS, [])
  const alerts = usePolling(api.alerts, REFRESH_MS, [])
  const [route, setRoute] = useState(parseHash)
  const [selected, setSelected] = useState<string | null>(() => parseHash().symbol ?? storedSymbol())
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)

  useEffect(() => {
    const onHash = () => {
      const r = parseHash()
      setRoute(r)
      if (r.symbol) setSelected(r.symbol)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // Si no hay selección (o el símbolo ya no está), elegir el primero de la watchlist.
  useEffect(() => {
    const list = quotes.data
    if (list && list.length && (!selected || !list.some((q) => q.symbol === selected))) setSelected(list[0].symbol)
  }, [quotes.data, selected])

  useEffect(() => {
    if (!selected) return
    if (route.view === 'mercado' && window.location.hash.slice(1) !== selected) history.replaceState(null, '', `#${selected}`)
    try {
      localStorage.setItem('symbol', selected)
    } catch {
      /* sin almacenamiento */
    }
  }, [selected, route.view])

  const go = (view: View) => {
    window.location.hash = view === 'mercado' ? (selected ?? '') : `/${view}`
  }
  const openSymbol = useCallback((s: string) => {
    window.location.hash = s
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
      alerts.reload()
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : String(e))
    } finally {
      setRefreshing(false)
    }
  }

  const tz = status.data?.timezone ?? 'America/Santiago'
  const cycleTime = status.data?.last_cycle
  const offline = status.error && !status.data && !LOCAL_MODE
  const nextTheme: Record<Theme, Theme> = { system: 'dark', dark: 'light', light: 'system' }
  const themeLabel: Record<Theme, string> = { system: 'Tema: sistema', dark: 'Tema: oscuro', light: 'Tema: claro' }
  const quote = quotes.data?.find((q) => q.symbol === selected)
  const unread = alerts.data?.unread ?? 0
  const unreadCritical = alerts.data?.by_severity.critical ?? 0
  const tabs: [View, string][] = [['mercado', 'Mercado'], ['cartera', 'Cartera'], ['alertas', 'Alertas']]

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
        <nav className="tabs" aria-label="Secciones">
          {tabs.map(([v, label]) => (
            <button key={v} aria-current={route.view === v ? 'page' : undefined} onClick={() => go(v)}>
              {label}
              {v === 'alertas' && unread > 0 && (
                <span className={`count${unreadCritical ? ' crit' : ''}`} aria-label={`${unread} sin leer`}>{unread}</span>
              )}
            </button>
          ))}
        </nav>
      </header>
      {unreadCritical > 0 && route.view !== 'alertas' && (
        <button className="crit-banner" onClick={() => go('alertas')}>
          ⛔ {unreadCritical === 1 ? 'Hay 1 alerta crítica sin leer' : `Hay ${unreadCritical} alertas críticas sin leer`}
          {' '}(caída bajo tu umbral de venta). Ver alertas →
        </button>
      )}
      {offline && (
        <p className="warn" role="alert" style={{ margin: '12px 16px' }}>
          No hay conexión con el servidor. Inícialo con <code>uvicorn app.main:app</code> en analisis/backend.
        </p>
      )}
      {refreshError && <p className="error" role="alert" style={{ margin: '8px 16px' }}>{refreshError}</p>}

      {route.view === 'mercado' && (
        <div className="layout">
          <div className="col">
            <Watchlist
              quotes={quotes.data}
              signals={signals.data?.signals ?? null}
              selected={selected}
              onSelect={openSymbol}
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
      )}
      {route.view === 'cartera' && <PortfolioPage onSelect={openSymbol} />}
      {route.view === 'alertas' && (
        <div className="col page">
          <AlertsView data={alerts.data} onChanged={alerts.reload} onSelect={openSymbol} />
        </div>
      )}
      <footer>{DISCLAIMER} Los datos pueden venir con retraso; revisa siempre la hora de cada precio.</footer>
    </>
  )
}
