import type { ReactNode } from 'react'
import { Icon, go, useRoute } from './components'
import { Alerts, AlertForm } from './screens/Alerts'
import { AssetForm } from './screens/AssetForm'
import { History } from './screens/History'
import { Home } from './screens/Home'
import { Import } from './screens/Import'
import { Portfolio, PositionDetail } from './screens/Portfolio'
import { Settings } from './screens/Settings'
import { TradeForm } from './screens/TradeForm'
import { useApp } from './store'

const tabs: [string, string, string][] = [
  ['inicio', 'Inicio', 'home'],
  ['portafolio', 'Portafolio', 'pie'],
  ['historial', 'Historial', 'list'],
  ['alertas', 'Alertas', 'bell'],
]

export function App() {
  const [name = '', arg, arg2] = useRoute()
  const { cur, other, setSettings, refresh, toast } = useApp()

  let title = 'Inicio'
  let tab = 'inicio'
  let back = ''
  let screen: ReactNode = <Home />
  switch (name) {
    case 'portafolio':
      ;[title, tab, screen] = ['Portafolio', 'portafolio', <Portfolio />]
      break
    case 'posicion':
      ;[title, tab, back, screen] = [arg, 'portafolio', 'portafolio', <PositionDetail key={arg} id={arg} />]
      break
    case 'historial':
      ;[title, tab, screen] = ['Historial', 'historial', <History />]
      break
    case 'nueva':
      ;[title, tab, back, screen] = ['Nueva operación', '', 'historial', <TradeForm key={arg} assetId={arg} />]
      break
    case 'activo':
      ;[title, tab, back, screen] = ['Nuevo activo', '', 'nueva', <AssetForm />]
      break
    case 'importar':
      ;[title, tab, back, screen] = ['Importar', 'historial', 'historial', <Import />]
      break
    case 'alertas':
      ;[title, tab, screen] = ['Alertas', 'alertas', <Alerts />]
      break
    case 'alerta':
      ;[title, tab, back] = [arg === 'nueva' ? 'Nueva alerta' : 'Editar alerta', 'alertas', 'alertas']
      screen = arg === 'nueva' ? <AlertForm key="nueva" assetId={arg2} /> : <AlertForm key={arg} id={arg} />
      break
    case 'ajustes':
      ;[title, tab, back, screen] = ['Ajustes', '', 'inicio', <Settings />]
      break
  }

  return (
    <div className="app">
      <header className="topbar">
        {back ? (
          <button className="icon-btn" onClick={() => go(back)} aria-label="Volver">
            <Icon name="back" />
          </button>
        ) : (
          <span className="icon-btn" />
        )}
        <h1>{title}</h1>
        <button className="cur-btn num" onClick={() => setSettings({ displayCurrency: other })} aria-label={`Moneda ${cur}. Cambiar a ${other}`}>
          {cur}
        </button>
        <button className="icon-btn" onClick={refresh} aria-label="Actualizar cotizaciones">
          <Icon name="refresh" />
        </button>
        <button className="icon-btn" onClick={() => go('ajustes')} aria-label="Ajustes">
          <Icon name="sliders" />
        </button>
      </header>

      <main>{screen}</main>

      {toast && <div className="toast" role="status">{toast}</div>}

      <nav className="tabbar" aria-label="Secciones">
        {tabs.slice(0, 2).map(tabButton)}
        <button className="fab" onClick={() => go('nueva')} aria-label="Nueva operación">
          <Icon name="plus" size={26} />
        </button>
        {tabs.slice(2).map(tabButton)}
      </nav>
    </div>
  )

  function tabButton([path, label, icon]: [string, string, string]) {
    return (
      <button key={path} className={tab === path ? 'on' : ''} aria-current={tab === path ? 'page' : undefined} onClick={() => go(path)}>
        <Icon name={icon} />
        <span>{label}</span>
      </button>
    )
  }
}
