import { useState, type FormEvent } from 'react'
import { AllocationBar, Delta, Field, Seg, Spark, go } from '../components'
import { sampleHistory } from '../data/seed'
import { parseNum } from '../lib/csv'
import { fmtDate, fmtMoney, fmtPct, fmtPrice, fmtQty, typeLabel } from '../lib/format'
import { useApp } from '../store'

export function Portfolio() {
  const { views, tot, cur } = useApp()
  const [by, setBy] = useState<'activo' | 'tipo'>('activo')

  const groups = new Map<string, number>()
  for (const v of views) {
    const k = by === 'activo' ? v.asset.id : typeLabel[v.asset.type]
    groups.set(k, (groups.get(k) ?? 0) + v.value[cur])
  }
  const items = [...groups]
    .map(([label, val]) => ({ label, pct: tot.value[cur] ? (val / tot.value[cur]) * 100 : 0 }))
    .sort((a, b) => b.pct - a.pct)

  return (
    <>
      {views.length > 0 && (
        <section className="card">
          <div className="between">
            <div className="label">Distribución</div>
            <Seg label="Agrupar por" value={by} onChange={setBy} options={[['activo', 'Por activo'], ['tipo', 'Por tipo']]} />
          </div>
          <AllocationBar items={items} />
        </section>
      )}

      <h2>Posiciones abiertas ({views.length})</h2>
      <section className="card list">
        {views.length === 0 && <p className="muted">Sin posiciones. Agrega tu primera operación con el botón +.</p>}
        {views.map((v) => (
          <button key={v.asset.id} className="row" onClick={() => go(`posicion/${v.asset.id}`)}>
            <div>
              <b>{v.asset.id}</b>
              <div className="muted num">
                {fmtQty(v.quantity)} × {fmtPrice(v.avgPrice, v.asset.currency)}
              </div>
            </div>
            <div className="right">
              <div className="num">{fmtMoney(v.value[cur], cur)}</div>
              <Delta value={v.pnl[cur]}>
                {fmtMoney(v.pnl[cur], cur, true)} ({fmtPct(v.pnlPct[cur])})
              </Delta>
            </div>
          </button>
        ))}
      </section>
      <p className="foot muted">
        El precio promedio incluye comisiones. La ganancia en {cur} considera el tipo de cambio de cada compra.
      </p>
    </>
  )
}

export function PositionDetail({ id }: { id: string }) {
  const { views, cur, state, asset, brokerName, ledger, setPrice } = useApp()
  const [editing, setEditing] = useState(false)
  const [newPrice, setNewPrice] = useState('')
  const a = asset(id)
  const v = views.find((x) => x.asset.id === id)
  const q = state.quotes[id]
  if (!a || !q) return <p className="muted">Activo no encontrado.</p>
  const dayPct = (q.price / q.prevClose - 1) * 100
  const hist = state.demo ? sampleHistory(id, q.price) : null
  const trades = state.trades.filter((t) => t.assetId === id).sort((x, y) => y.date.localeCompare(x.date))
  const tradable = a.type !== 'indice' && a.type !== 'divisa'
  const parsed = parseNum(newPrice)

  const savePrice = (e: FormEvent) => {
    e.preventDefault()
    if (parsed === null || parsed <= 0) return
    setPrice(id, parsed)
    setEditing(false)
    setNewPrice('')
  }

  return (
    <>
      <section className="card">
        <div className="label">
          {a.name} · {a.exchange}
        </div>
        <div className="big num">{fmtPrice(q.price, a.currency)}</div>
        <div className="between">
          <Delta value={dayPct}>{fmtPct(dayPct)} hoy</Delta>
          <button className="link" onClick={() => setEditing(!editing)} aria-expanded={editing}>Editar precio</button>
        </div>
        <div className="muted small">Precio del {fmtDate(q.asOf, true)}</div>
        {editing && (
          <form className="form mt" onSubmit={savePrice}>
            <Field label={`Precio actual (${a.currency})`}>
              <input inputMode="decimal" value={newPrice} onChange={(e) => setNewPrice(e.target.value)} autoFocus />
            </Field>
            <button className="btn primary" disabled={parsed === null || parsed <= 0}>Guardar precio</button>
          </form>
        )}
        {hist && (
          <>
            <Spark data={hist} up={hist[hist.length - 1] >= hist[0]} />
            <div className="muted small">Últimos 90 días (serie de ejemplo)</div>
          </>
        )}
      </section>

      {v ? (
        <section className="card stats">
          <div><span className="label">Cantidad</span><b className="num">{fmtQty(v.quantity)}</b></div>
          <div><span className="label">Precio promedio</span><b className="num">{fmtPrice(v.avgPrice, a.currency)}</b></div>
          <div><span className="label">Costo</span><b className="num">{fmtMoney(v.cost[cur], cur)}</b></div>
          <div><span className="label">Valor actual</span><b className="num">{fmtMoney(v.value[cur], cur)}</b></div>
          <div>
            <span className="label">G/P no realizada</span>
            <Delta value={v.pnl[cur]}>{fmtMoney(v.pnl[cur], cur, true)} ({fmtPct(v.pnlPct[cur])})</Delta>
          </div>
          <div>
            <span className="label">G/P realizada</span>
            <Delta value={v.realized[cur]}>{fmtMoney(v.realized[cur], cur, true)}</Delta>
          </div>
        </section>
      ) : (
        tradable && <p className="muted">No tienes posición abierta en este activo.</p>
      )}

      <div className="grid2">
        {tradable && <button className="btn" onClick={() => go(`nueva/${id}`)}>Registrar operación</button>}
        <button className="btn" onClick={() => go(`alerta/nueva/${id}`)}>Crear alerta</button>
      </div>

      {trades.length > 0 && (
        <>
          <h2>Operaciones</h2>
          <section className="card list">
            {trades.map((t) => {
              const r = ledger.realizedByTrade[t.id]
              return (
                <div key={t.id} className="row">
                  <div>
                    <span className={`chip ${t.side}`}>{t.side}</span> <span className="num">{fmtQty(t.quantity)} × {fmtPrice(t.price, t.currency)}</span>
                    <div className="muted">{fmtDate(t.date)} · {brokerName(t.brokerId)}</div>
                  </div>
                  {r && (
                    <div className="right">
                      <Delta value={r[cur]}>{fmtMoney(r[cur], cur, true)}</Delta>
                    </div>
                  )}
                </div>
              )
            })}
          </section>
        </>
      )}
    </>
  )
}
