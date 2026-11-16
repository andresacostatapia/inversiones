import { Delta, go } from '../components'
import { fmtDate, fmtMoney, fmtPct, fmtPrice } from '../lib/format'
import { useApp } from '../store'

export function Home() {
  const { tot, cur, other, views, state, asset, fx } = useApp()
  const movers = [...views].sort((a, b) => Math.abs(b.dayPct) - Math.abs(a.dayPct)).slice(0, 3)
  // Momento del precio más reciente entre los activos (sin contar el dólar, que es diario).
  const asOf = Object.values(state.quotes)
    .filter((q) => q.assetId !== 'USDCLP')
    .map((q) => q.asOf)
    .sort()
    .at(-1)

  return (
    <>
      <section className="card">
        <div className="label">Valor del portafolio</div>
        <div className="big num">{fmtMoney(tot.value[cur], cur)}</div>
        <div className="muted num">
          ≈ {fmtMoney(tot.value[other], other)} ·{' '}
          <button className="link" onClick={() => go('posicion/USDCLP')}>dólar {fmtPrice(fx, 'CLP')}</button>
        </div>
        <div className="grid2 mt">
          <div>
            <div className="label">Hoy</div>
            <Delta value={tot.day[cur]}>{fmtMoney(tot.day[cur], cur, true)}</Delta>
            <div>
              <Delta value={tot.dayPct[cur]}>{fmtPct(tot.dayPct[cur])}</Delta>
            </div>
          </div>
          <div>
            <div className="label">Desde el inicio</div>
            <Delta value={tot.sinceStart[cur]}>{fmtMoney(tot.sinceStart[cur], cur, true)}</Delta>
            <div>
              <Delta value={tot.sinceStartPct[cur]}>{fmtPct(tot.sinceStartPct[cur])}</Delta>
            </div>
          </div>
        </div>
      </section>

      <h2>
        Alertas recientes <button className="link" onClick={() => go('alertas')}>Ver todas</button>
      </h2>
      <section className="card list">
        {state.events.length === 0 && <p className="muted">Ninguna alerta se ha cumplido todavía.</p>}
        {state.events.slice(0, 3).map((e) => {
          const a = asset(e.assetId)
          const isPct = e.message.startsWith('Variación')
          return (
            <button key={e.id} className="row" onClick={() => go('alertas')}>
              <div>
                <b>{e.assetId}</b> · {e.message}
                <div className="muted">{fmtDate(e.triggeredAt, true)}</div>
              </div>
              <div className="num right">{isPct ? fmtPct(e.value) : fmtPrice(e.value, a?.currency ?? 'USD')}</div>
            </button>
          )
        })}
      </section>

      <h2>
        Movimientos del día <button className="link" onClick={() => go('portafolio')}>Portafolio</button>
      </h2>
      <section className="card list">
        {movers.length === 0 && <p className="muted">Aún no tienes posiciones. Registra tu primera operación con el botón +.</p>}
        {movers.map((v) => (
          <button key={v.asset.id} className="row" onClick={() => go(`posicion/${v.asset.id}`)}>
            <div>
              <b>{v.asset.id}</b>
              <div className="muted">{v.asset.name}</div>
            </div>
            <div className="right">
              <div className="num">{fmtPrice(v.price, v.asset.currency)}</div>
              <Delta value={v.dayPct}>{fmtPct(v.dayPct)}</Delta>
            </div>
          </button>
        ))}
      </section>

      <p className="foot muted">
        {state.demo ? 'Cotizaciones de ejemplo' : 'Último precio'}{asOf ? ` · ${fmtDate(asOf, true)}` : ''}
        {state.demo ? '' : ' · la bolsa chilena llega con 15 min de retraso'}. Esta app solo informa y registra: no
        ejecuta órdenes.
      </p>
    </>
  )
}
