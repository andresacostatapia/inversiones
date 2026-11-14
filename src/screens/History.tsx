import { useState } from 'react'
import { Delta, Field, go } from '../components'
import { brokers } from '../data/seed'
import { download, toCsv } from '../lib/csv'
import { fmtDate, fmtMoney, fmtPrice, fmtQty, today } from '../lib/format'
import { useApp } from '../store'

export function History() {
  const { state, cur, ledger, brokerName, deleteTrade, assets } = useApp()
  const [f, setF] = useState({ asset: '', side: '', broker: '', from: '', to: '' })
  const [showFilters, setShowFilters] = useState(false)
  const [open, setOpen] = useState('')
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })

  const rows = state.trades
    .filter(
      (t) =>
        (!f.asset || t.assetId === f.asset) &&
        (!f.side || t.side === f.side) &&
        (!f.broker || t.brokerId === f.broker) &&
        (!f.from || t.date >= f.from) &&
        (!f.to || t.date <= f.to),
    )
    .sort((a, b) => b.date.localeCompare(a.date))
  const active = Object.values(f).filter(Boolean).length
  const realized = rows.reduce((s, t) => s + (ledger.realizedByTrade[t.id]?.[cur] ?? 0), 0)

  const exportCsv = () =>
    download(
      `operaciones-${today()}.csv`,
      toCsv([
        ['Fecha', 'Activo', 'Tipo', 'Cantidad', 'Precio', 'Comisión', 'Impuestos', 'Moneda', 'Tipo de cambio', 'Broker', 'G/P realizada CLP', 'G/P realizada USD', 'Nota'],
        ...rows.map((t) => {
          const r = ledger.realizedByTrade[t.id]
          return [t.date, t.assetId, t.side, t.quantity, t.price, t.fee, t.tax, t.currency, t.fxRate, brokerName(t.brokerId), r ? Math.round(r.CLP) : '', r ? r.USD.toFixed(2) : '', t.note]
        }),
      ]),
    )

  let lastMonth = ''
  return (
    <>
      <section className="card">
        <div className="between">
          <div>
            <div className="label">Ganancia realizada {active ? '(filtrada)' : 'acumulada'}</div>
            <Delta value={realized}><b>{fmtMoney(realized, cur, true)}</b></Delta>
          </div>
          <div className="muted num">{rows.length} operaciones</div>
        </div>
        <div className="grid3 mt">
          <button className="btn" onClick={() => setShowFilters(!showFilters)}>Filtros{active ? ` (${active})` : ''}</button>
          <button className="btn" onClick={exportCsv}>Exportar CSV</button>
          <button className="btn" onClick={() => go('importar')}>Importar</button>
        </div>
        {showFilters && (
          <div className="form mt">
            <div className="grid2">
              <Field label="Activo">
                <select value={f.asset} onChange={set('asset')}>
                  <option value="">Todos</option>
                  {assets.map((a) => <option key={a.id}>{a.id}</option>)}
                </select>
              </Field>
              <Field label="Tipo">
                <select value={f.side} onChange={set('side')}>
                  <option value="">Todos</option>
                  <option value="compra">Compra</option>
                  <option value="venta">Venta</option>
                </select>
              </Field>
            </div>
            <Field label="Broker">
              <select value={f.broker} onChange={set('broker')}>
                <option value="">Todos</option>
                {brokers.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </Field>
            <div className="grid2">
              <Field label="Desde"><input type="date" value={f.from} onChange={set('from')} /></Field>
              <Field label="Hasta"><input type="date" value={f.to} onChange={set('to')} /></Field>
            </div>
            {active > 0 && <button className="link" onClick={() => setF({ asset: '', side: '', broker: '', from: '', to: '' })}>Limpiar filtros</button>}
          </div>
        )}
      </section>

      {rows.length === 0 && <p className="muted">No hay operaciones con esos filtros.</p>}
      <section className="timeline">
        {rows.map((t) => {
          const month = new Intl.DateTimeFormat('es-CL', { month: 'long', year: 'numeric' }).format(new Date(t.date + 'T12:00:00'))
          const head = month !== lastMonth
          lastMonth = month
          const r = ledger.realizedByTrade[t.id]
          const total = t.quantity * t.price + (t.side === 'compra' ? 1 : -1) * (t.fee + t.tax)
          return (
            <div key={t.id}>
              {head && <h2>{month}</h2>}
              <div className="card list tight">
                <button className="row" onClick={() => setOpen(open === t.id ? '' : t.id)} aria-expanded={open === t.id}>
                  <div>
                    <span className={`chip ${t.side}`}>{t.side}</span> <b>{t.assetId}</b>
                    <div className="muted num">{fmtDate(t.date)} · {fmtQty(t.quantity)} × {fmtPrice(t.price, t.currency)}</div>
                  </div>
                  <div className="right">
                    <div className="num">{fmtMoney(total, t.currency)}</div>
                    {r && <Delta value={r[cur]}>{fmtMoney(r[cur], cur, true)}</Delta>}
                  </div>
                </button>
                {open === t.id && (
                  <div className="detail">
                    <div className="num">
                      Broker: {brokerName(t.brokerId)} · Comisión: {fmtMoney(t.fee, t.currency)} · Impuestos: {fmtMoney(t.tax, t.currency)} · Dólar: {fmtPrice(t.fxRate, 'CLP')}
                    </div>
                    {t.note && <div>Nota: {t.note}</div>}
                    {t.importBatchId && <div className="muted">Importada desde archivo</div>}
                    <button className="link danger" onClick={() => confirm('¿Eliminar esta operación? No se puede deshacer.') && deleteTrade(t.id)}>
                      Eliminar operación
                    </button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </section>
    </>
  )
}
